import json
import os
from unittest.mock import MagicMock

from hetznerinstance.main import (
    CACHE_DIR,
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    CONFIG_DIR,
    DEFAULT_IMAGE,
    DEFAULT_LOCATION,
    DEFAULT_SERVER_TYPE,
    LOCATIONS,
    PROGRAM_NAME,
    dump_json_with_datetimes,
    get_arch_from_name,
    guess_image_default_username,
    list_locations,
    list_server_types,
    load_pricing_date,
    load_server_types,
    load_token_from_hcloud_config,
    path_collapseuser,
    server_to_dict,
    slugify,
)


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert slugify("foo@bar#baz") == "foobarbaz"

    def test_spaces_and_dashes(self):
        assert slugify("  foo -- bar  ") == "foo-bar"

    def test_unicode(self):
        result = slugify("café résumé")
        assert "cafe" in result
        assert "resume" in result

    def test_empty(self):
        assert slugify("") == ""


class TestGetArchFromName:
    def test_arm_cax(self):
        assert get_arch_from_name("cax21") == "arm"

    def test_arm_in_name(self):
        assert get_arch_from_name("arm-something") == "arm"

    def test_x86_cx(self):
        assert get_arch_from_name("cx23") == "x86"

    def test_x86_cpx(self):
        assert get_arch_from_name("cpx31") == "x86"

    def test_x86_ccx(self):
        assert get_arch_from_name("ccx23") == "x86"


class TestGuessImageDefaultUsername:
    def test_ubuntu_string(self):
        assert guess_image_default_username("ubuntu") == "root"

    def test_ubuntu_name(self):
        assert guess_image_default_username("ubuntu-24.04") == "root"

    def test_debian_string(self):
        assert guess_image_default_username("debian") == "root"

    def test_debian_name(self):
        assert guess_image_default_username("debian-12") == "root"

    def test_fedora_string(self):
        assert guess_image_default_username("fedora") == "root"

    def test_fallback(self):
        assert guess_image_default_username("rocky-9") == "root"


class TestDumpJsonWithDatetimes:
    def test_basic(self):
        result = dump_json_with_datetimes({"key": "value"})
        parsed = json.loads(result)
        assert parsed["key"] == "value"

    def test_datetime(self):
        import datetime

        dt = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        result = dump_json_with_datetimes({"ts": dt})
        parsed = json.loads(result)
        assert parsed["ts"] == "2024-01-15T12:00:00.000Z"


class TestPathCollapseuser:
    def test_collapses(self):
        home = os.path.expanduser("~")
        path = os.path.join(home, "some", "path")
        result = path_collapseuser(path)
        assert result == "~/some/path"

    def test_no_collapse_needed(self):
        result = path_collapseuser("/tmp/something")
        assert result == "/tmp/something"


class TestServerToDict:
    def test_basic(self):
        server = MagicMock()
        server.id = 12345
        server.name = "test-server"
        server.status = "running"
        server.created = "2024-01-15T00:00:00Z"
        server.primary_disk_size = 20
        server.labels = {"env": "test"}

        server.server_type = MagicMock()
        server.server_type.id = 1
        server.server_type.name = "cx23"

        server.image = MagicMock()
        server.image.id = 42
        server.image.name = "ubuntu-24.04"
        server.image.os_flavor = "ubuntu"
        server.image.os_version = "24.04"

        server.location = MagicMock()
        server.location.id = 1
        server.location.name = "fsn1"

        server.datacenter = MagicMock()
        server.datacenter.id = 1
        server.datacenter.name = "fsn1-dc14"

        ipv4 = MagicMock()
        ipv4.ip = "1.2.3.4"
        ipv4.blocked = False
        ipv6 = MagicMock()
        ipv6.ip = "2a01:4f8::1"
        ipv6.blocked = False

        public_net = MagicMock()
        public_net.ipv4 = ipv4
        public_net.ipv6 = ipv6
        server.public_net = public_net

        result = server_to_dict(server)

        assert result["id"] == 12345
        assert result["name"] == "test-server"
        assert result["status"] == "running"
        assert result["server_type"]["name"] == "cx23"
        assert result["image"]["name"] == "ubuntu-24.04"
        assert result["location"]["name"] == "fsn1"
        assert result["public_net"]["ipv4"]["ip"] == "1.2.3.4"
        assert result["public_net"]["ipv6"]["ip"] == "2a01:4f8::1"
        assert result["primary_disk_size"] == 20

    def test_minimal_server(self):
        server = MagicMock()
        server.id = 99999
        server.name = "minimal"
        server.status = "off"
        server.created = None
        server.primary_disk_size = None
        server.labels = None
        server.server_type = None
        server.image = None
        server.location = None
        server.datacenter = None
        server.public_net = None

        result = server_to_dict(server)

        assert result["id"] == 99999
        assert result["name"] == "minimal"
        assert result["server_type"] == {"id": None, "name": None}
        assert result["public_net"] == {}


class TestDefaults:
    def test_default_server_type(self):
        assert DEFAULT_SERVER_TYPE == "cx23"

    def test_default_image(self):
        assert DEFAULT_IMAGE == "ubuntu"

    def test_default_location(self):
        assert DEFAULT_LOCATION == "fsn1"

    def test_program_name(self):
        assert PROGRAM_NAME == "hetznerinstance"

    def test_uses_shared_config_directory(self):
        assert CONFIG_DIR.endswith("ec2instance_cmd")

    def test_uses_shared_cache_directory(self):
        assert CACHE_DIR.endswith("ec2instance_cmd")


class TestHardcodedData:
    def test_locations_exist(self):
        assert len(LOCATIONS) == 6
        for code in ["fsn1", "nbg1", "hel1", "ash", "hil", "sin"]:
            assert code in LOCATIONS
            assert "city" in LOCATIONS[code]
            assert "country" in LOCATIONS[code]

    def test_server_types_exist(self):
        server_types = load_server_types()
        assert len(server_types) > 20
        for name, info in server_types.items():
            assert "category" in info
            assert "desc" in info
            assert "locations" in info
            assert len(info["locations"]) > 0
            for loc, prices in info["locations"].items():
                assert "monthly" in prices
                assert "hourly" in prices
                assert prices["monthly"] > 0
                assert prices["hourly"] > 0

    def test_cx23_in_server_types(self):
        server_types = load_server_types()
        assert "cx23" in server_types
        assert server_types["cx23"]["category"] in CATEGORY_ORDER
        assert "fsn1" in server_types["cx23"]["locations"]

    def test_category_order(self):
        assert CATEGORY_ORDER == ["cost-optimized", "regular", "dedicated"]

    def test_category_labels(self):
        for cat in CATEGORY_ORDER:
            assert cat in CATEGORY_LABELS

    def test_pricing_date_exists(self):
        pricing_date = load_pricing_date()
        assert pricing_date is not None
        assert len(pricing_date) >= 7  # e.g. '2026-04' or '2026-04-20'
        assert "20" in pricing_date  # starts with century


class TestListServerTypes:
    def test_list_all(self, capsys):
        list_server_types()
        captured = capsys.readouterr()
        assert "cx23" in captured.out
        assert load_pricing_date() in captured.out
        assert "not live" in captured.out
        assert "cax11" in captured.out
        assert "ccx13" in captured.out
        assert "Cost-optimized" in captured.out
        assert "Dedicated" in captured.out
        assert "€" in captured.out

    def test_list_with_location_filter(self, capsys):
        list_server_types(location_filter="ash")
        captured = capsys.readouterr()
        # cx23 is EU-only, should not appear as a server type name in ash filter
        # Check it doesn't appear as a type entry (not as part of other text)
        lines = captured.out.strip().split("\n")
        type_lines = [line for line in lines if line.strip() and not line.strip().startswith("-") and "(" in line]
        type_names = [line.strip().split()[0] for line in type_lines]
        assert "cx23" not in type_names
        # cpx11 is available in ash
        assert "cpx11" in type_names
        assert "ccx13" in type_names

    def test_list_with_eu_location(self, capsys):
        list_server_types(location_filter="fsn1")
        captured = capsys.readouterr()
        assert "cx23" in captured.out
        assert "cpx11" in captured.out


class TestListLocations:
    def test_list_locations(self, capsys):
        list_locations()
        captured = capsys.readouterr()
        assert "fsn1" in captured.out
        assert "Falkenstein" in captured.out
        assert "Germany" in captured.out
        assert "ash" in captured.out
        assert "Ashburn" in captured.out
        assert "sin" in captured.out
        assert "Singapore" in captured.out


class TestLoadTokenFromHcloudConfig:
    def test_no_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HCLOUD_CONFIG", str(tmp_path / "nonexistent.toml"))
        assert load_token_from_hcloud_config() is None

    def test_valid_config_default_context(self, tmp_path, monkeypatch):
        config_file = tmp_path / "cli.toml"
        config_file.write_text(
            'active_context = "default"\n\n' "[[contexts]]\n" '  name = "default"\n' '  token = "my-secret-token"\n'
        )
        monkeypatch.setenv("HCLOUD_CONFIG", str(config_file))
        assert load_token_from_hcloud_config() == "my-secret-token"

    def test_valid_config_named_context(self, tmp_path, monkeypatch):
        config_file = tmp_path / "cli.toml"
        config_file.write_text(
            'active_context = "prod"\n\n'
            "[[contexts]]\n"
            '  name = "default"\n'
            '  token = "default-token"\n'
            "\n\n"
            "[[contexts]]\n"
            '  name = "prod"\n'
            '  token = "prod-token"\n'
        )
        monkeypatch.setenv("HCLOUD_CONFIG", str(config_file))
        # Without specifying context, should use active_context
        assert load_token_from_hcloud_config() == "prod-token"
        # Specifying context name
        assert load_token_from_hcloud_config(context_name="default") == "default-token"
        assert load_token_from_hcloud_config(context_name="prod") == "prod-token"

    def test_unknown_context_falls_back_to_first(self, tmp_path, monkeypatch):
        config_file = tmp_path / "cli.toml"
        config_file.write_text("[[contexts]]\n" '  name = "default"\n' '  token = "first-token"\n')
        monkeypatch.setenv("HCLOUD_CONFIG", str(config_file))
        # No active_context set, no context_name specified -> first context
        assert load_token_from_hcloud_config() == "first-token"
        # Non-existent context -> first context
        assert load_token_from_hcloud_config(context_name="nonexistent") is None

    def test_empty_contexts(self, tmp_path, monkeypatch):
        config_file = tmp_path / "cli.toml"
        config_file.write_text('active_context = "default"\n')
        monkeypatch.setenv("HCLOUD_CONFIG", str(config_file))
        assert load_token_from_hcloud_config() is None

    def test_invalid_toml(self, tmp_path, monkeypatch):
        config_file = tmp_path / "cli.toml"
        config_file.write_text("this is not valid toml {{{")
        monkeypatch.setenv("HCLOUD_CONFIG", str(config_file))
        assert load_token_from_hcloud_config() is None

    def test_hcloud_config_env_var(self, tmp_path, monkeypatch):
        # Verify that HCLOUD_CONFIG env var overrides default path
        config_file = tmp_path / "custom.toml"
        config_file.write_text("[[contexts]]\n" '  name = "default"\n' '  token = "env-var-token"\n')
        monkeypatch.setenv("HCLOUD_CONFIG", str(config_file))
        assert load_token_from_hcloud_config() == "env-var-token"
