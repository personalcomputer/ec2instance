import pytest
from unittest.mock import patch
from moto import mock_aws
import boto3
from ec2instance.main import launch_instance, get_ami, get_vpc, get_subnet, get_security_group, get_keypair

@pytest.fixture
def ec2_client():
    with mock_aws():
        client = boto3.client("ec2", region_name="us-west-2")
        yield client

def test_launch_instance(ec2_client):
    # Mock data
    ami = "ami-0819a13b911cc3dd0"
    subnet_id = "subnet-12345678"
    security_group_id = "sg-12345678"
    instance_type = "t3.micro"
    keypair_name = "test-keypair"
    user_data = "#!/bin/bash\necho 'Hello World'"
    volume_size = 10

    # Create VPC
    vpc = ec2_client.create_vpc(CidrBlock="172.30.0.0/16")
    vpc_id = vpc['Vpc']['VpcId']

    # Register AMI in mock environment
    ec2_client.register_image(
        Name="ubuntu-focal-20.04-amd64-server",
        ImageId=ami,
        RootDeviceName="/dev/sda1",
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "VolumeSize": volume_size,
                    "DeleteOnTermination": True,
                    "VolumeType": "gp2",
                },
            }
        ],
    )
    ec2_client.create_subnet(VpcId=vpc_id, CidrBlock="172.30.90.0/24")
    ec2_client.create_security_group(GroupName="test-sg", Description="test", VpcId=vpc_id)
    ec2_client.create_key_pair(KeyName=keypair_name)

    # Launch instance
    instance = launch_instance(
        ec2_client=ec2_client,
        ami=ami,
        subnet_id=subnet_id,
        security_group_id=security_group_id,
        instance_type=instance_type,
        keypair_name=keypair_name,
        user_data=user_data,
        volume_size=volume_size,
    )

    assert instance["InstanceId"] is not None
    assert instance["InstanceType"] == instance_type

def test_get_ami(ec2_client):
    with patch("ec2instance.main.get_latest_ubuntu_lts_ami", return_value="ami-ubuntu"):
        ami = get_ami(ec2_client, "ubuntu", "amd64")
        assert ami == "ami-ubuntu"

def test_get_vpc(ec2_client):
    vpc_id = get_vpc(ec2_client)
    assert vpc_id is not None

def test_get_subnet(ec2_client):
    vpc_id = get_vpc(ec2_client)
    subnet_id = get_subnet(ec2_client, vpc_id)
    assert subnet_id is not None

def test_get_security_group(ec2_client):
    vpc_id = get_vpc(ec2_client)
    security_group_id = get_security_group(ec2_client, vpc_id)
    assert security_group_id is not None

def test_get_keypair(ec2_client):
    keypair_name, key_path = get_keypair(ec2_client)
    assert keypair_name is not None
    assert key_path is not None
