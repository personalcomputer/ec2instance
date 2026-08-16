import datetime
import json

import pytest

from ec2instance.core import build_instance_result, dump_json_with_datetimes, load_user_data


def test_dump_json_normalizes_datetime_to_utc():
    value = datetime.datetime(
        2024,
        1,
        15,
        14,
        tzinfo=datetime.timezone(datetime.timedelta(hours=2)),
    )
    assert json.loads(dump_json_with_datetimes({"at": value}))["at"] == "2024-01-15T12:00:00.000Z"


def test_dump_json_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone"):
        dump_json_with_datetimes({"at": datetime.datetime(2024, 1, 15)})


def test_load_user_data_creates_and_reads_default(tmp_path):
    default = tmp_path / "scripts" / "default.sh"
    result = load_user_data(str(default), str(default), str(default.parent))
    assert result.startswith("#!/bin/bash")
    assert default.read_text() == result


def test_build_instance_result_has_stable_shape():
    result = build_instance_result(
        provider="aws",
        resource_id="i-123",
        status="running",
        host="203.0.113.1",
        port=22,
        user="ubuntu",
        instance_type="t3.micro",
        image="ami-123",
        location="us-west-2a",
        raw={"InstanceId": "i-123"},
    )
    assert result["provider"] == "aws"
    assert result["ssh"] == {"host": "203.0.113.1", "port": 22, "user": "ubuntu"}
    assert result["raw"]["InstanceId"] == "i-123"
