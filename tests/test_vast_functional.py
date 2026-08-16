import json
import os

import pytest

from vastinstance.main import (
    CONFIG_DIR,
    DEFAULT_AMI,
    IMAGE_TEMPLATES_PATH,
    PROGRAM_NAME,
    compute_runtype,
    dump_json_with_datetimes,
    get_ssh_key_path,
    instance_to_dict,
    list_templates,
    parse_env,
    path_collapseuser,
    resolve_ssh_endpoint,
    resolve_template,
    smart_split,
)


class TestSmartSplit:
    def test_basic(self):
        assert smart_split("a b c", " ") == ["a", "b", "c"]

    def test_respects_single_quotes(self):
        assert smart_split("-p 'a b'", " ") == ["-p", "'a b'"]

    def test_respects_double_quotes(self):
        assert smart_split('-p "a b"', " ") == ["-p", '"a b"']


class TestParseEnv:
    def test_port(self):
        assert parse_env("-p 1111:1111") == {"-p 1111:1111": "1"}

    def test_port_udp(self):
        assert parse_env("-p 3478:3478/udp") == {"-p 3478:3478/udp": "1"}

    def test_env_var(self):
        assert parse_env('-e KEY="1"') == {"KEY": "1"}

    def test_env_var_with_equals_in_value(self):
        assert parse_env("-e PORTAL_CONFIG=localhost:1111:11111") == {"PORTAL_CONFIG": "localhost:1111:11111"}

    def test_multiple(self):
        result = parse_env("-p 1111:1111 -e KEY=1 -e OTHER=2")
        assert result == {"-p 1111:1111": "1", "KEY": "1", "OTHER": "2"}

    def test_quoted_value_with_spaces(self):
        result = parse_env('-e PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal"')
        assert result == {"PORTAL_CONFIG": "localhost:1111:11111:/:Instance Portal"}

    def test_none(self):
        assert parse_env(None) == {}

    def test_full_nvidia_cuda_template_env(self):
        env = (
            "-p 1111:1111 -p 6006:6006 -p 8080:8080 -p 8384:8384 -p 10100:10100 "
            '-p 10200:10200 -p 72299:72299 -e OPEN_BUTTON_PORT="1111" '
            '-e OPEN_BUTTON_TOKEN="1" -e JUPYTER_DIR="/" -e DATA_DIRECTORY="/workspace/" '
            '-e PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:8080:18080:/:Jupyter"'
        )
        result = parse_env(env)
        assert result["-p 1111:1111"] == "1"
        assert result["-p 6006:6006"] == "1"
        assert result["OPEN_BUTTON_PORT"] == "1111"
        assert result["JUPYTER_DIR"] == "/"
        assert "|localhost" in result["PORTAL_CONFIG"]


class TestComputeRuntype:
    def test_ssh_direct(self):
        assert compute_runtype(jupyter=False, ssh=True, direct=True) == "ssh_direc ssh_proxy"

    def test_ssh_proxy(self):
        assert compute_runtype(jupyter=False, ssh=True, direct=False) == "ssh_proxy"

    def test_jupyter_direct(self):
        assert compute_runtype(jupyter=True, ssh=True, direct=True) == "jupyter_direc ssh_direc ssh_proxy"

    def test_jupyter_proxy(self):
        assert compute_runtype(jupyter=True, ssh=True, direct=False) == "jupyter_proxy ssh_proxy"

    def test_none(self):
        assert compute_runtype(jupyter=False, ssh=False, direct=True) is None


class TestImageTemplates:
    def test_templates_load(self):
        with open(IMAGE_TEMPLATES_PATH) as f:
            templates = json.load(f)
        assert "nvidia-cuda" in templates
        assert "linux-desktop-container" in templates
        assert "ubuntu-desktop-vm" in templates

    def test_template_fields(self):
        for t in _all_templates().values():
            assert "image" in t
            assert "env" in t
            assert "disk" in t and t["disk"] > 0
            assert "ssh" in t
            assert "direct" in t

    def test_resolve_template(self):
        t = resolve_template("nvidia-cuda")
        assert t["image"] == "vastai/base-image:@vastai-automatic-tag"
        assert t["disk"] == 16
        assert t["jupyter"] is True
        assert t["ssh"] is True

    def test_resolve_unknown(self):
        with pytest.raises(ValueError):
            resolve_template("does-not-exist")

    def test_ubuntu_desktop_vm_is_ssh_only(self):
        t = resolve_template("ubuntu-desktop-vm")
        assert t["jupyter"] is False
        assert t["ssh"] is True
        assert t["disk"] == 75

    def test_template_env_parses_cleanly(self):
        for name, t in _all_templates().items():
            result = parse_env(t["env"])
            assert len(result) > 0, name


def _all_templates():
    with open(IMAGE_TEMPLATES_PATH) as f:
        return json.load(f)


class TestInstanceToDict:
    def test_basic(self):
        inst = {
            "id": 42,
            "actual_status": "running",
            "ssh_host": "1.2.3.4",
            "ssh_port": 22,
            "num_gpus": 1,
            "gpu_name": "RTX 4090",
            "dph_total": 0.5,
            "unused_field": "ignored",
        }
        result = instance_to_dict(inst)
        assert result["id"] == 42
        assert result["actual_status"] == "running"
        assert result["ssh_host"] == "1.2.3.4"
        assert "unused_field" not in result

    def test_empty(self):
        assert instance_to_dict(None) == {}
        assert instance_to_dict({}) == {}


class TestResolveSshEndpoint:
    def test_direct_connection_preferred(self):
        inst = {
            "public_ipaddr": "203.0.113.7",
            "ports": {"22/tcp": [{"HostPort": "40022"}]},
            "ssh_host": "ssh5.vast.ai",
            "ssh_port": 12345,
        }
        assert resolve_ssh_endpoint(inst) == ("203.0.113.7", 40022)

    def test_direct_host_port_as_int(self):
        inst = {
            "public_ipaddr": "203.0.113.7",
            "ports": {"22/tcp": [{"HostPort": 40022}]},
        }
        assert resolve_ssh_endpoint(inst) == ("203.0.113.7", 40022)

    def test_falls_back_to_proxy_without_direct_port(self):
        inst = {"ssh_host": "ssh5.vast.ai", "ssh_port": 12345}
        assert resolve_ssh_endpoint(inst) == ("ssh5.vast.ai", 12345)

    def test_falls_back_to_proxy_when_host_port_missing(self):
        inst = {
            "public_ipaddr": "203.0.113.7",
            "ports": {"22/tcp": [{}]},
            "ssh_host": "ssh5.vast.ai",
            "ssh_port": 12345,
        }
        assert resolve_ssh_endpoint(inst) == ("ssh5.vast.ai", 12345)

    def test_falls_back_to_proxy_when_22_tcp_empty(self):
        inst = {
            "public_ipaddr": "203.0.113.7",
            "ports": {"22/tcp": []},
            "ssh_host": "ssh5.vast.ai",
            "ssh_port": 12345,
        }
        assert resolve_ssh_endpoint(inst) == ("ssh5.vast.ai", 12345)

    def test_falls_back_to_proxy_when_no_public_ip(self):
        inst = {
            "ports": {"22/tcp": [{"HostPort": "40022"}]},
            "ssh_host": "ssh5.vast.ai",
            "ssh_port": 12345,
        }
        assert resolve_ssh_endpoint(inst) == ("ssh5.vast.ai", 12345)

    def test_none_when_neither_available(self):
        assert resolve_ssh_endpoint({}) == (None, None)

    def test_none_when_only_ssh_host(self):
        assert resolve_ssh_endpoint({"ssh_host": "ssh5.vast.ai"}) == (None, None)

    def test_handles_bad_host_port_gracefully(self):
        inst = {
            "public_ipaddr": "203.0.113.7",
            "ports": {"22/tcp": [{"HostPort": "not-a-number"}]},
            "ssh_host": "ssh5.vast.ai",
            "ssh_port": 12345,
        }
        assert resolve_ssh_endpoint(inst) == ("ssh5.vast.ai", 12345)


class TestDumpJsonWithDatetimes:
    def test_basic(self):
        result = dump_json_with_datetimes({"key": "value"})
        assert json.loads(result)["key"] == "value"

    def test_datetime(self):
        import datetime

        dt = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        result = dump_json_with_datetimes({"ts": dt})
        assert json.loads(result)["ts"] == "2024-01-15T12:00:00.000Z"


class TestPathCollapseuser:
    def test_collapses(self):
        home = os.path.expanduser("~")
        result = path_collapseuser(os.path.join(home, "some", "path"))
        assert result == "~/some/path"

    def test_no_collapse(self):
        assert path_collapseuser("/tmp/something") == "/tmp/something"


class TestDefaults:
    def test_default_ami(self):
        assert DEFAULT_AMI == "nvidia-cuda"

    def test_program_name(self):
        assert PROGRAM_NAME == "vastinstance"

    def test_uses_shared_config_directory(self):
        assert CONFIG_DIR.endswith("ec2instance_cmd")


class TestGetSshKeyPath:
    def test_returns_none_when_no_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "vastinstance.main.os.path.expanduser",
            lambda path: str(tmp_path / path.removeprefix("~/")),
        )
        assert get_ssh_key_path() is None

    def test_finds_ed25519(self, tmp_path, monkeypatch):
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "id_ed25519").write_text("KEY")
        monkeypatch.setattr(
            "vastinstance.main.os.path.expanduser",
            lambda path: str(tmp_path / path.removeprefix("~/")),
        )
        assert get_ssh_key_path() == str(ssh_dir / "id_ed25519")


class TestListTemplates:
    def test_lists_all(self, capsys):
        list_templates()
        captured = capsys.readouterr()
        assert "nvidia-cuda" in captured.out
        assert "linux-desktop-container" in captured.out
        assert "ubuntu-desktop-vm" in captured.out
        assert "Template" in captured.out
