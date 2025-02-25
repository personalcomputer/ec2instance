import pytest
from unittest.mock import patch
from moto import mock_aws
import boto3
from ec2instance.main import main, launch_instance, get_ami, get_vpc, get_subnet, get_security_group, get_keypair

@pytest.fixture
def ec2_client():
    with mock_aws():
        client = boto3.client("ec2", region_name="us-west-2")
        yield client

def test_cli_launch_default(ec2_client, capsys):
    # Simulate CLI command to launch an instance with default settings
    cli_command = "ec2instance.main"
    with patch("sys.argv", cli_command.split()):
        main()
    captured = capsys.readouterr()
    assert "Instance Launched!" in captured.out

def test_cli_launch_specific_type_non_interactive(ec2_client, capsys):
    # Simulate CLI command to launch an instance with a specific type and non-interactive mode
    cli_command = "ec2instance.main --type t2.micro --non-interactive"
    with patch("sys.argv", cli_command.split()):
        main()
    captured = capsys.readouterr()
    assert "Instance Launched!" in captured.out
    assert "{" in captured.out  # Check for JSON output

def test_cli_launch_custom_user_data(ec2_client, capsys):
    # Simulate CLI command to launch an instance with a custom user data script
    cli_command = "ec2instance.main --user-data custom_script.sh"
    with patch("sys.argv", cli_command.split()):
        main()
    captured = capsys.readouterr()
    assert "Instance Launched!" in captured.out

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
