import pytest
from unittest.mock import patch
from moto import mock_aws
import boto3
from ec2instance.main import main


@pytest.fixture
def ec2_client():
    with mock_aws():
        client = boto3.client("ec2", region_name="us-west-2")
        yield client


@mock_ec2
def test_cli_launch_default(capsys):
    # Mock the launch_instance function
    with patch("ec2instance.main.launch_instance") as mock_launch_instance:
        mock_launch_instance.return_value = {
            "InstanceId": "i-1234567890abcdef0",
            "InstanceType": "t3.micro",
            "PublicIpAddress": "192.0.2.0"
        }
        # Simulate CLI command to launch an instance with default settings
        cli_command = "ec2instance.main"
        with patch("sys.argv", cli_command.split()):
            main()
        captured = capsys.readouterr()
        assert "Instance Launched!" in captured.out
    # Simulate CLI command to launch an instance with default settings
    cli_command = "ec2instance.main"
    with patch("sys.argv", cli_command.split()):
        main()
    captured = capsys.readouterr()
    assert "Instance Launched!" in captured.out


@mock_ec2
def test_cli_launch_specific_type_non_interactive(capsys):
    # Mock the launch_instance function
    with patch("ec2instance.main.launch_instance") as mock_launch_instance:
        mock_launch_instance.return_value = {
            "InstanceId": "i-1234567890abcdef0",
            "InstanceType": "t2.micro",
            "PublicIpAddress": "192.0.2.0"
        }
        # Simulate CLI command to launch an instance with a specific type and non-interactive mode
        cli_command = "ec2instance.main --type t2.micro --non-interactive"
        with patch("sys.argv", cli_command.split()):
            main()
        captured = capsys.readouterr()
        assert "Instance Launched!" in captured.out
        assert "{" in captured.out  # Check for JSON output
    # Simulate CLI command to launch an instance with a specific type and non-interactive mode
    cli_command = "ec2instance.main --type t2.micro --non-interactive"
    with patch("sys.argv", cli_command.split()):
        main()
    captured = capsys.readouterr()
    assert "Instance Launched!" in captured.out
    assert "{" in captured.out  # Check for JSON output


@mock_ec2
def test_cli_launch_custom_user_data(capsys):
    # Mock the launch_instance function
    with patch("ec2instance.main.launch_instance") as mock_launch_instance:
        mock_launch_instance.return_value = {
            "InstanceId": "i-1234567890abcdef0",
            "InstanceType": "t3.micro",
            "PublicIpAddress": "192.0.2.0"
        }
        # Simulate CLI command to launch an instance with a custom user data script
        cli_command = "ec2instance.main --user-data custom_script.sh"
        with patch("sys.argv", cli_command.split()):
            main()
        captured = capsys.readouterr()
        assert "Instance Launched!" in captured.out
    # Simulate CLI command to launch an instance with a custom user data script
    cli_command = "ec2instance.main --user-data custom_script.sh"
    with patch("sys.argv", cli_command.split()):
        main()
    captured = capsys.readouterr()
    assert "Instance Launched!" in captured.out


def test_launch_instance(ec2_client):
    # Mock data
    instance_type = "t3.micro"
    keypair_name = "test-keypair"
    user_data = "#!/bin/bash\necho 'Hello World'"
    volume_size = 10

    # Create VPC
    vpc = ec2_client.create_vpc(CidrBlock="172.30.0.0/16")
    vpc_id = vpc['Vpc']['VpcId']

    # Register AMI in mock environment
    ami = ec2_client.register_image(
        Name="ubuntu-focal-20.04-amd64-server",
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
    )['ImageId']
    subnet = ec2_client.create_subnet(VpcId=vpc_id, CidrBlock="172.30.90.0/24")
    subnet_id = subnet['Subnet']['SubnetId']
    security_group = ec2_client.create_security_group(GroupName="test-sg", Description="test", VpcId=vpc_id)
    security_group_id = security_group['GroupId']
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
