import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def ec2_client():
    with mock_aws():
        client = boto3.client("ec2", region_name="us-west-2")
        yield client


def test_launch_instance(ec2_client):
    from ec2instance.main import launch_instance

    # Mock data
    instance_type = "t3.micro"
    keypair_name = "test-keypair"
    user_data = "#!/bin/bash\necho 'Hello World'\n"
    # Mock the custom user data script
    with open("custom_script.sh", "w") as f:
        f.write(user_data)
    volume_size = 10

    # Create VPC
    vpc = ec2_client.create_vpc(CidrBlock="172.30.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]

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
    )["ImageId"]
    subnet = ec2_client.create_subnet(VpcId=vpc_id, CidrBlock="172.30.90.0/24")
    subnet_id = subnet["Subnet"]["SubnetId"]
    security_group = ec2_client.create_security_group(GroupName="test-sg", Description="test", VpcId=vpc_id)
    security_group_id = security_group["GroupId"]
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
