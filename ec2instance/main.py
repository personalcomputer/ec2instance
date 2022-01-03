#!/usr/bin/env python3
import argparse
import datetime
import logging
import os
import re
import shutil
import signal
import socket
import sys
import time
import json
import unicodedata

import boto3
import botocore.exceptions
from Cryptodome.PublicKey import RSA
from iso8601 import parse_date as parse_iso8601

PROGRAM_NAME = 'ec2instance_cmd'
HOSTNAME = socket.gethostname()
USERNAME = os.environ.get('USER', '')
XDG_CONFIG_HOME = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
CONFIG_DIR = os.path.join(XDG_CONFIG_HOME, PROGRAM_NAME)

DEFAULT_USER_DATA = '''#!/bin/bash
date

# Disable MOTD
for userdir in /home/*; do touch $userdir/.hushlogin; done

if grep -qi "Ubuntu" /etc/issue; then
    apt update -y
fi
'''
USER_DATA_SCRIPTS_LIBRARY_PATH = os.path.join(CONFIG_DIR, 'user_data_scripts')
DEFAULT_USER_DATA_PATH = os.path.join(USER_DATA_SCRIPTS_LIBRARY_PATH, 'default.sh')
DEFAULT_INSTANCE_TYPE = 't3a.micro'


def slugify(value):
    """
    Limits the text to just letters + numbers, with spaces converted to "-".
    Credit to Django: https://github.com/django/django/blob/master/django/utils/text.py
    """
    value = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    return re.sub(r'[-\s]+', '-', value)


def get_latest_ubuntu_lts_ami(ec2_client, arch):
    response = ec2_client.describe_images(
        Filters=[
            {
                'Name': 'name',
                'Values': [f'ubuntu/images/hvm-ssd/ubuntu-*-{arch}-server-*'],
            },
            {
                'Name': 'description',
                'Values': ['*LTS*'],
            },
        ],
        Owners=['099720109477'],  # Canonical Group Limited
    )
    amis = response['Images']
    # sort to get latest ubuntu version with latest creation date

    def sorting_key_func(image):
        ubuntu_version_number = re.search(r'-([\d\.]+)-' + arch, image['Name']).group(1)
        image_creation_date = parse_iso8601(image['CreationDate'])
        return float(ubuntu_version_number), image_creation_date
    amis = sorted(amis, key=sorting_key_func, reverse=True)
    # return top (most recent) ami
    return amis[0]['ImageId']


def get_latest_amazonlinux_ami(ec2_client, arch):
    if arch == 'amd64':
        arch = 'x86_64'
    else:
        raise NotImplementedError
    response = ec2_client.describe_images(
        Filters=[
            {
                'Name': 'name',
                'Values': [f'amzn2-ami-hvm-*-{arch}-gp2'],
            },
            {
                'Name': 'state',
                'Values': ['available'],
            },
        ],
        Owners=['amazon'],
    )
    amis = response['Images']
    # sort to get latest creation date
    amis = sorted(amis, key=lambda image: parse_iso8601(image['CreationDate']), reverse=True)
    # return top (most recent) ami
    return amis[0]['ImageId']


def get_ami(ec2_client, ami_identifier, arch):
    if ami_identifier == 'ubuntu':
        return get_latest_ubuntu_lts_ami(ec2_client, arch)
    elif ami_identifier == 'amazonlinux':
        return get_latest_amazonlinux_ami(ec2_client, arch)
    else:
        if not ami_identifier.startswith('ami-'):
            raise ValueError(f'unrecognized ami id \'{ami_identifier}\'')
        return ami_identifier


def guess_ami_default_username(ami_identifier):
    if ami_identifier == 'ubuntu':
        return 'ubuntu'
    elif ami_identifier == 'amazonlinux':
        return 'ec2-user'
    return 'core'


def get_arch(instance_type):
    if re.match(r'^[a-z]\dg\.', instance_type):
        return 'arm64'
    return 'amd64'


def get_ssh_bin():
    if shutil.which('sshrc'):
        return 'sshrc'
    return 'ssh'


def get_vpc(ec2_client):
    vpc_name = slugify(f'{PROGRAM_NAME} auto-created VPC')
    vpcs = ec2_client.describe_vpcs(
        Filters=[{'Name': 'tag:Name', 'Values': [vpc_name]}]
    )['Vpcs']
    if vpcs:
        vpc_id = vpcs[0]['VpcId']
    else:
        logging.info(
            'NOTICE: This appears to be the first time you are running ec2instance on this machine. Welcome! To '
            'prevent any conflicts or security concerns with your other AWS resources, ec2instance needs to create a '
            'dedicated new VPC and SSH key for itself (one-time setup).'
        )
        logging.info(f'Creating prerequisite VPC (one-time setup): "{vpc_name}"...')
        vpc = ec2_client.create_vpc(
            CidrBlock='172.30.90.0/24',
            AmazonProvidedIpv6CidrBlock=True,
        )
        vpc_id = vpc['Vpc']['VpcId']
        tags = {
            'Name': vpc_name,
        }
        ec2_client.create_tags(
            Resources=[vpc['Vpc']['VpcId']],
            Tags=[{'Key': key, 'Value': value} for key, value in tags.items()]
        )
        igw_id = ec2_client.create_internet_gateway()['InternetGateway']['InternetGatewayId']
        ec2_client.attach_internet_gateway(
            InternetGatewayId=igw_id,
            VpcId=vpc_id,
        )
        route_table_id = ec2_client.describe_route_tables(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
            ],
        )['RouteTables'][0]['RouteTableId']
        ec2_client.create_route(
            DestinationCidrBlock='0.0.0.0/0',
            GatewayId=igw_id,
            RouteTableId=route_table_id,
        )
    return vpc_id


def get_subnet(ec2_client, vpc_id):
    subnets = ec2_client.describe_subnets(
        Filters=[{
            'Name': 'vpc-id',
            'Values': [vpc_id],
        }]
    )['Subnets']
    if subnets:
        subnet_id = subnets[0]['SubnetId']
    else:
        # Pick availability zone most likely to support the most instance types: The "b" zone of the region.
        availability_zones = ec2_client.describe_availability_zones()['AvailabilityZones']
        availability_zone_name = next(zone for zone in availability_zones if zone['ZoneName'].endswith('b'))['ZoneName']

        subnet_id = ec2_client.create_subnet(
            CidrBlock='172.30.90.0/24',
            AvailabilityZone=availability_zone_name,
            VpcId=vpc_id,
        )['Subnet']['SubnetId']
        ec2_client.modify_subnet_attribute(
            SubnetId=subnet_id,
            MapPublicIpOnLaunch={'Value': True},
        )
    return subnet_id


def get_security_group(ec2_client, vpc_id):
    security_group_name = slugify(f'{PROGRAM_NAME} auto-created security group')
    security_groups = ec2_client.describe_security_groups(
        Filters=[{
            'Name': 'group-name',
            'Values': [security_group_name],
        }]
    )['SecurityGroups']
    if security_groups:
        security_group_id = security_groups[0]['GroupId']
    else:
        security_group_id = ec2_client.create_security_group(
            Description=security_group_name,
            GroupName=security_group_name,
            VpcId=vpc_id,
        )['GroupId']
        ec2_client.authorize_security_group_ingress(
            CidrIp='0.0.0.0/0',
            GroupId=security_group_id,
            IpProtocol='-1',
        )
    return security_group_id


def get_keypair(ec2_client):
    """
    Returns (keypair_name, key_path)
    """
    keypair_name = slugify(f'{PROGRAM_NAME} {HOSTNAME} {USERNAME} auto-created key')
    key_path = os.path.join(CONFIG_DIR, f'{keypair_name}.pem')
    legacy_key_path = os.path.join(CONFIG_DIR, 'key.pem')
    if not os.path.exists(key_path) and os.path.exists(legacy_key_path):
        key_path = legacy_key_path
    keypair_exists = bool(ec2_client.describe_key_pairs(
        Filters=[{'Name': 'key-name', 'Values': [keypair_name]}]
    )['KeyPairs'])  # TODO: Switch to matching the keypair via key fingerprint, not key name.
    if keypair_exists:
        if not os.path.exists(key_path):
            raise ValueError('Not able to handle this situation - keypair matching this hostname is already uploaded, '
                             'but does not exist locally. Aborting')
    else:
        if os.path.exists(key_path):
            logging.info('Uploading prerequisite keypair...')
            with open(key_path, 'rb') as f:
                pub_key = RSA.importKey(f.read()).publickey().exportKey(format='OpenSSH').decode()
            ec2_client.import_key_pair(
                KeyName=keypair_name,
                PublicKeyMaterial=pub_key
            )
        else:
            logging.info('Generating prerequisite SSH keypair...')
            response = ec2_client.create_key_pair(KeyName=keypair_name, KeyType='ed25519')
            # AWS create_key_pair API is a secure way to generate a brand new SSH private/public keypair without relying
            # upon e.g. openssh being installed locally. docs:
            # https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateKeyPair.html
            os.makedirs(os.path.dirname(key_path), exist_ok=True)
            with open(os.open(key_path, os.O_CREAT | os.O_WRONLY, 0o600), 'w') as f:
                f.write(response['KeyMaterial'])
    return keypair_name, key_path


def launch_instance(ec2_client, ami, subnet_id, security_group_id, instance_type, keypair_name, user_data, volume_size):
    # Prepare RunInstances configuration
    ec2_instance_name = f'{PROGRAM_NAME} {HOSTNAME} {USERNAME} {datetime.datetime.utcnow().isoformat()}'
    run_instances_kwargs = {
        'ImageId': ami,
        'SubnetId': subnet_id,
        'SecurityGroupIds': [security_group_id],
        'MaxCount': 1,
        'MinCount': 1,
        'InstanceType': instance_type,
        'KeyName': keypair_name,
        'UserData': user_data,
        'TagSpecifications': [{
            'ResourceType': 'instance',
            'Tags': [
                {'Key': 'Name', 'Value': ec2_instance_name}
            ]
        }],
    }
    if re.match(r'^t\da?\.', instance_type):
        run_instances_kwargs['CreditSpecification'] = {'CpuCredits': 'unlimited'}
    if volume_size is not None:
        block_device_mappings = \
            ec2_client.describe_images(ImageIds=[ami])['Images'][0]['BlockDeviceMappings']
        block_device_mappings[0]['Ebs']['VolumeSize'] = volume_size
        run_instances_kwargs['BlockDeviceMappings'] = block_device_mappings

    # Launch
    instance_id = ec2_client.run_instances(**run_instances_kwargs)['Instances'][0]['InstanceId']
    ec2_client.get_waiter('instance_running').wait(InstanceIds=[instance_id], WaiterConfig={'Delay': 3})

    # Return details of the launching instance
    return ec2_client.describe_instances(InstanceIds=[instance_id])['Reservations'][0]['Instances'][0]


def wait_until_accepts_connection(ip, port):
    POLLING_INTERVAL_S = 1.5
    while True:
        try:
            s = socket.create_connection((ip, port), timeout=POLLING_INTERVAL_S)
        except (ConnectionRefusedError, ConnectionResetError):
            time.sleep(POLLING_INTERVAL_S)
        except (socket.timeout):
            pass
        else:
            s.close()
            return


def terminate_instance(ec2_client, instance_id):
    ec2_client.terminate_instances(
        InstanceIds=[instance_id, ]
    )
    logging.info('Instance is terminating.')


def terminate(ec2_client, instance_id):
    logging.info('Terminating instance...')
    terminate_instance(ec2_client, instance_id)
    sys.exit(0)


quit = False


def handle_interrupted_launch():
    """ Handle SIGINT """
    logging.info('Will terminate instance immediately after launch. Please wait a few more seconds...')
    global quit
    quit = True


def main():
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
    logging.getLogger('botocore').setLevel(logging.WARNING)
    arg_parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Quickly launch an EC2 instance for small tasks. The instance\'s '
                    'lifecycle is tied to the process,\nenabling easy Ctrl+C instance termination when done.',
        epilog='help & support:\n  https://github.com/personalcomputer/ec2instance/issues'
    )
    arg_parser.add_argument('-t', '--type', type=str, default=DEFAULT_INSTANCE_TYPE, dest='instance_type',
                            help=f'EC2 instance type. (default: {DEFAULT_INSTANCE_TYPE})')
    arg_parser.add_argument('-i', '--ami', type=str, default='ubuntu', dest='ami_identifier',
                            help='EC2 AMI id. You may also pass "ubuntu" as a shortcut to get the latest Ubuntu LTS, or'
                                 ' "amazonlinux" as a shortcut to get the latest Amazon Linux. (default: ubuntu)')
    arg_parser.add_argument('-f', '--user-data', type=str, default=DEFAULT_USER_DATA_PATH, dest='user_data_filename',
                            help='EC2 user data. Path to a shell script. AWS will upload and run this script '
                                 f'on the instance immediately after launch. (default: {DEFAULT_USER_DATA_PATH})')
    arg_parser.add_argument('--volume-size', type=int, default=None, dest='volume_size',
                            help='Root EBS volume size (GiB).')
    arg_parser.add_argument('--profile', type=str, default=None, dest='profile_name',
                            help='AWS credentials profile name to use.')
    arg_parser.add_argument('--region', type=str, default=None, dest='aws_region',
                            help='Specific AWS region to use.')
    arg_parser.add_argument('--non-interactive', action='store_true', dest='non_interactive',
                            help='By default a shell will be opened in the spawned instance, and the '
                                 'instance will be terminated when the shell is closed. To instead '
                                 'output ec2 metadata as json and then exit, specify --non-interactive.')
    arg_parser.add_argument('--show-data-path', action='store_true', help='Print out the path where ec2instance is '
                            'storing local data and configuration.')
    args = arg_parser.parse_args()

    if args.show_data_path:
        print(CONFIG_DIR)
        sys.exit(0)

    # Load user data
    if not os.path.exists(DEFAULT_USER_DATA_PATH):
        os.makedirs(os.path.dirname(DEFAULT_USER_DATA_PATH), exist_ok=True)
        with open(DEFAULT_USER_DATA_PATH, 'w') as f:
            f.write(DEFAULT_USER_DATA)
    user_data_rel_path = args.user_data_filename
    user_data_lib_path = os.path.join(USER_DATA_SCRIPTS_LIBRARY_PATH, args.user_data_filename)
    if os.path.exists(user_data_rel_path):
        user_data_path = user_data_rel_path
    elif os.path.exists(user_data_lib_path):
        user_data_path = user_data_lib_path
    else:
        raise ValueError(f'Cannot open {args.user_data_filename}')
    with open(user_data_path) as f:
        user_data = f.read()

    # AWS client init
    try:
        boto3_session = boto3.session.Session(profile_name=args.profile_name, region_name=args.aws_region)
        ec2_client = boto3_session.client('ec2')
        # Use region-less STS get caller identity API just as a hack to verify boot3 was able to load credentials. This
        # is important to check so that we can give the right guidance to very inexperienced AWS users.
        boto3.client('sts').get_caller_identity()
    except botocore.exceptions.NoCredentialsError:
        logging.error(
            'Unable to locate AWS credentials!\n\nIf you\'re not sure what to do, please follow these '
            'steps to resolve:\n'
            ' - 1.) Install awscli, https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html\n'
            ' - 2.) Run `aws configure` and enter in your AWS access key. How to get an AWS access key is not in '
            'scope of this guide.\n'
        )
        sys.exit(1)
    except botocore.exceptions.NoRegionError:
        logging.error(
            'No default AWS region specified!\n\n If you\'re not sure what to do, to resolve please re-run `aws '
            'configure` and specify a region when prompted (e.g. us-west-2), or pass a region in on the command line '
            'using --region.'
        )
        sys.exit(1)

    # Check/provision AWS pre-reqs
    ami = get_ami(ec2_client, args.ami_identifier, arch=get_arch(args.instance_type))
    vpc_id = get_vpc(ec2_client)
    subnet_id = get_subnet(ec2_client, vpc_id)
    security_group_id = get_security_group(ec2_client, vpc_id)
    keypair_name, key_path = get_keypair(ec2_client)

    # Launch
    logging.info('Launching instance... (ETA to usability: ~45 seconds)')
    signal.signal(signal.SIGINT, lambda a, b: handle_interrupted_launch())
    signal.signal(signal.SIGTERM, lambda a, b: handle_interrupted_launch())
    instance = launch_instance(
        ec2_client=ec2_client,
        ami=ami,
        subnet_id=subnet_id,
        security_group_id=security_group_id,
        instance_type=args.instance_type,
        keypair_name=keypair_name,
        user_data=user_data,
        volume_size=args.volume_size,
    )
    instance_id = instance['InstanceId']
    instance_ip = instance['PublicIpAddress']
    if quit:
        terminate(ec2_client, instance_id)
        return
    signal.signal(signal.SIGINT, lambda a, b: terminate(ec2_client, instance_id))
    signal.signal(signal.SIGTERM, lambda a, b: terminate(ec2_client, instance_id))

    logging.info(f'Instance Launched! ({instance_id}) Waiting for instance to finish booting... '
                 '(ETA to usability: ~25 seconds)')
    wait_until_accepts_connection(ip=instance_ip, port=22)
    logging.info('Instance is up!')

    if args.non_interactive:
        print(json.dumps(instance, indent=2))
        return

    # Launch Shell
    logging.info('Launching shell...')
    time.sleep(6)  # Wait an extra six seconds for the userdata script to run, to silence MOTD.
    ssh_login_user = guess_ami_default_username(args.ami_identifier)
    ssh_args = [get_ssh_bin(), '-i', key_path, f'{ssh_login_user}@{instance_ip}']
    ssh_cmd = ' '.join(ssh_args)
    print(ssh_cmd)
    automatic_ssh_cmd = ' '.join(ssh_args + ['-o', 'StrictHostKeyChecking=no'])
    os.system(automatic_ssh_cmd)

    # Wait for SIGTERM/SIGINT
    logging.info(f'Instance is still running. Press CTRL+C to terminate, or the command to SSH again is: {ssh_cmd}')
    while True:
        signal.pause()


if __name__ == '__main__':
    main()
