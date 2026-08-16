#!/usr/bin/env python3
import datetime
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tomllib
import unicodedata

from cryptography.hazmat.primitives import serialization
from hcloud import Client
from hcloud.firewalls import Firewall, FirewallRule
from hcloud.images import Image
from hcloud.locations import Location
from hcloud.server_types import ServerType
from hcloud.servers import BoundServer, ServerCreatePublicNetwork
from hcloud.ssh_keys import SSHKey

from ec2instance.core import (
    PROGRAM_CACHE_DIR,
    PROGRAM_CONFIG_DIR,
    build_instance_result,
    dump_json_with_datetimes,
    list_and_validate_scripts,
    load_user_data,
    path_collapseuser,
    resolve_provisioning_script,
    run_provisioning_script,
    wait_until_accepts_connection,
)

PROGRAM_NAME = "hetznerinstance"
HOSTNAME = socket.gethostname()
USERNAME = os.environ.get("USER", "")
XDG_CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
CONFIG_DIR = str(PROGRAM_CONFIG_DIR)
CACHE_DIR = str(PROGRAM_CACHE_DIR)

USER_DATA_SCRIPTS_LIBRARY_PATH = os.path.join(CONFIG_DIR, "user_data_scripts")
DEFAULT_USER_DATA_PATH = os.path.join(USER_DATA_SCRIPTS_LIBRARY_PATH, "default.sh")
DEFAULT_SERVER_TYPE = "cx23"
DEFAULT_IMAGE = "ubuntu"
DEFAULT_LOCATION = "fsn1"

PRIMARY_IPV4_MONTHLY_EUR = 0.50

HCLOUD_CONFIG_DIR = os.environ.get("HCLOUD_CONFIG_DIR", os.path.join(XDG_CONFIG_HOME, "hcloud"))
HCLOUD_CONFIG_PATH = os.path.join(HCLOUD_CONFIG_DIR, "cli.toml")

# Hardcoded location metadata. Key = Hetzner Cloud location code.
LOCATIONS = {
    "fsn1": {"city": "Falkenstein", "country": "Germany", "region": "Europe"},
    "nbg1": {"city": "Nuremberg", "country": "Germany", "region": "Europe"},
    "hel1": {"city": "Helsinki", "country": "Finland", "region": "Europe"},
    "ash": {"city": "Ashburn, VA", "country": "USA", "region": "North America"},
    "hil": {"city": "Hillsboro, OR", "country": "USA", "region": "North America"},
    "sin": {"city": "Singapore", "country": "Singapore", "region": "Asia-Pacific"},
}

PRICING_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pricing.json")

_PRICING_DATA = None


def _get_pricing_data() -> dict:
    global _PRICING_DATA
    if _PRICING_DATA is None:
        try:
            with open(PRICING_JSON_PATH) as f:
                _PRICING_DATA = json.load(f)
        except Exception:
            _PRICING_DATA = {}
    return _PRICING_DATA


def load_pricing_date() -> str:
    return _get_pricing_data().get("pricing_date", "unknown")


def load_server_types() -> dict:
    return _get_pricing_data().get("server_types", {})


CATEGORY_ORDER = ["cost-optimized", "regular", "dedicated"]
CATEGORY_LABELS = {
    "cost-optimized": "Cost-optimized (shared vCPU, limited availability)",
    "regular": "Regular (shared vCPU, all locations)",
    "dedicated": "Dedicated (dedicated vCPU, all locations)",
}


def slugify(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)


def load_token_from_hcloud_config(context_name: str | None = None) -> str | None:
    config_path = os.environ.get("HCLOUD_CONFIG") or HCLOUD_CONFIG_PATH
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except Exception:
        return None
    contexts = config.get("contexts", [])
    if not contexts:
        return None
    # If a specific context was requested, only look for that one
    if context_name:
        for ctx in contexts:
            if ctx.get("name") == context_name:
                return ctx.get("token")
        return None
    # Otherwise use the active_context from the config
    active_context = config.get("active_context")
    if active_context:
        for ctx in contexts:
            if ctx.get("name") == active_context:
                return ctx.get("token")
    # Fallback: return token from first context
    return contexts[0].get("token")


def list_server_types(location_filter: str | None = None):
    server_types = load_server_types()
    print()
    print("  Pricing data as of %s (not live, may be outdated). Update with: update_pricing.py" % load_pricing_date())
    for cat in CATEGORY_ORDER:
        types_in_cat = [(name, info) for name, info in server_types.items() if info["category"] == cat]
        if not types_in_cat:
            continue
        print()
        print("  %s" % CATEGORY_LABELS[cat])
        print("  %s" % ("-" * len(CATEGORY_LABELS[cat])))
        for name, info in sorted(
            types_in_cat,
            key=lambda x: (
                x[1]["locations"].get(location_filter or "fsn1", x[1]["locations"].get("fsn1", {})).get("monthly", 9999)
            ),
        ):
            locs = info["locations"]
            if location_filter:
                if location_filter not in locs:
                    continue
                price = locs[location_filter]
                print("  %-8s  %-42s  €%7.2f/mo  (€%.4f/hr)" % (name, info["desc"], price["monthly"], price["hourly"]))
            else:
                eu_locs = [loc for loc in locs if loc in ("fsn1", "nbg1", "hel1")]
                us_locs = [loc for loc in locs if loc in ("ash", "hil")]
                ap_locs = [loc for loc in locs if loc in ("sin",)]
                price = locs.get("fsn1", locs.get("nbg1", locs.get("hel1", list(locs.values())[0])))
                avail = ", ".join(eu_locs + us_locs + ap_locs) or "check availability"
                print(
                    "  %-8s  %-42s  €%7.2f/mo  (€%.4f/hr)  [%s]"
                    % (name, info["desc"], price["monthly"], price["hourly"], avail)
                )
    print()


def list_locations():
    print()
    print("  %-6s  %-18s  %-14s  %-14s" % ("Code", "City", "Country", "Region"))
    print("  %-6s  %-18s  %-14s  %-14s" % ("-----", "----", "-------", "------"))
    for code, info in LOCATIONS.items():
        print("  %-6s  %-18s  %-14s  %-14s" % (code, info["city"], info["country"], info["region"]))
    print()


def _latest_ubuntu_cache_path(arch: str) -> str:
    return os.path.join(CACHE_DIR, "latest-ubuntu-%s.json" % arch)


def _load_cached_latest_ubuntu(client: Client, arch: str) -> Image | None:
    cache_path = _latest_ubuntu_cache_path(arch)
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path) as f:
            cached = json.load(f)
        cached_at = datetime.datetime.fromisoformat(cached["cached_at"])
        age_s = (datetime.datetime.now(tz=datetime.timezone.utc) - cached_at).total_seconds()
        if age_s > 604800:  # 1 week TTL
            return None
        image_id = int(cached["image_id"])
        return client.images.get_by_id(image_id)
    except Exception:
        return None


def _save_cached_latest_ubuntu(arch: str, image: Image) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = _latest_ubuntu_cache_path(arch)
    data = {
        "image_id": image.id,
        "image_name": image.name,
        "cached_at": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
    }
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)


def get_image(client: Client, image_identifier: str, arch: str) -> Image:
    if image_identifier == "ubuntu":
        cached_image = _load_cached_latest_ubuntu(client, arch)
        if cached_image is not None:
            return cached_image
        images = client.images.get_list(name=None, architecture=[arch], type=["system"], sort=["created:desc"])
        all_images = images.images
        ubuntu_images = [i for i in all_images if i.name and "ubuntu" in i.name]
        if ubuntu_images:
            image = ubuntu_images[0]
            _save_cached_latest_ubuntu(arch, image)
            return image
        raise ValueError("No Ubuntu system image found for architecture '%s'" % arch)
    elif image_identifier == "debian":
        images = client.images.get_list(name=None, architecture=[arch], type=["system"], sort=["created:desc"])
        all_images = images.images
        debian_images = [i for i in all_images if i.name and "debian" in i.name]
        if debian_images:
            return debian_images[0]
        raise ValueError("No Debian system image found for architecture '%s'" % arch)
    elif image_identifier == "fedora":
        images = client.images.get_list(name=None, architecture=[arch], type=["system"], sort=["created:desc"])
        all_images = images.images
        fedora_images = [i for i in all_images if i.name and "fedora" in i.name]
        if fedora_images:
            return fedora_images[0]
        raise ValueError("No Fedora system image found for architecture '%s'" % arch)
    else:
        images = client.images.get_list(name=image_identifier, architecture=[arch])
        if images.images:
            return images.images[0]
        try:
            image_id = int(image_identifier)
            return client.images.get_by_id(image_id)
        except (ValueError, Exception):
            pass
        raise ValueError("Unrecognized image identifier '%s'" % image_identifier)


def guess_image_default_username(image_identifier: str) -> str:
    if image_identifier == "ubuntu" or (isinstance(image_identifier, str) and "ubuntu" in image_identifier):
        return "root"
    elif image_identifier == "debian" or (isinstance(image_identifier, str) and "debian" in image_identifier):
        return "root"
    elif image_identifier == "fedora" or (isinstance(image_identifier, str) and "fedora" in image_identifier):
        return "root"
    return "root"


def get_arch_from_name(server_type_name: str) -> str:
    if "cax" in server_type_name or "arm" in server_type_name:
        return "arm"
    return "x86"


def get_ssh_bin() -> str:
    if shutil.which("sshrc"):
        return "sshrc"
    return "ssh"


def get_or_create_firewall(client: Client) -> Firewall:
    firewall_name = slugify(f"{PROGRAM_NAME} auto-created firewall")
    existing = client.firewalls.get_list(name=firewall_name)
    if existing.firewalls:
        return existing.firewalls[0]
    logging.info("Creating prerequisite firewall: '%s'..." % firewall_name)
    rules = [
        FirewallRule(
            direction="in",
            protocol="tcp",
            port="22",
            source_ips=["0.0.0.0/0", "::/0"],
            description="Allow SSH",
        ),
        FirewallRule(
            direction="in",
            protocol="tcp",
            port="0-65535",
            source_ips=["0.0.0.0/0", "::/0"],
            description="Allow TCP in",
        ),
        FirewallRule(
            direction="in",
            protocol="udp",
            port="0-65535",
            source_ips=["0.0.0.0/0", "::/0"],
            description="Allow UDP in",
        ),
        FirewallRule(
            direction="in",
            protocol="icmp",
            source_ips=["0.0.0.0/0", "::/0"],
            description="Allow ICMP",
        ),
    ]
    resp = client.firewalls.create(name=firewall_name, rules=rules)
    return resp.firewall


def get_or_create_ssh_key(client: Client) -> tuple[str, str]:
    keypair_name = slugify(f"{PROGRAM_NAME} {HOSTNAME} {USERNAME} auto-created key")
    key_path = os.path.join(CONFIG_DIR, f"{keypair_name}.pem")
    legacy_key_path = os.path.join(CONFIG_DIR, "key.pem")
    if not os.path.exists(key_path) and os.path.exists(legacy_key_path):
        key_path = legacy_key_path

    existing = client.ssh_keys.get_by_name(keypair_name)
    if existing:
        if not os.path.exists(key_path):
            raise ValueError(
                "Keypair matching this hostname is already uploaded to Hetzner Cloud, "
                "but does not exist locally at '%s'. Aborting." % key_path
            )
        return keypair_name, key_path

    if os.path.exists(key_path):
        logging.info("Uploading prerequisite SSH keypair...")
        with open(key_path, "rb") as f:
            priv_key = serialization.load_ssh_private_key(f.read(), password=None)
            pub_key = (
                priv_key.public_key()
                .public_bytes(
                    encoding=serialization.Encoding.OpenSSH,
                    format=serialization.PublicFormat.OpenSSH,
                )
                .decode()
            )
        client.ssh_keys.create(name=keypair_name, public_key=pub_key)
    else:
        logging.info("Generating prerequisite SSH keypair...")
        from cryptography.hazmat.primitives.asymmetric import ed25519

        private_key = ed25519.Ed25519PrivateKey.generate()
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_key = (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH,
            )
            .decode()
        )
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        with open(os.open(key_path, os.O_CREAT | os.O_WRONLY, 0o600), "wb") as f:
            f.write(private_pem)
        client.ssh_keys.create(name=keypair_name, public_key=public_key)

    return keypair_name, key_path


def launch_server(
    client: Client,
    image: Image,
    server_type: ServerType,
    location: Location,
    ssh_keys: list[SSHKey],
    firewalls: list[Firewall],
    user_data: str,
    server_name: str,
    public_net: ServerCreatePublicNetwork | None = None,
) -> BoundServer:
    kwargs: dict = dict(
        name=server_name,
        server_type=server_type,
        image=image,
        ssh_keys=ssh_keys,
        firewalls=firewalls,
        user_data=user_data,
        location=location,
        start_after_create=True,
    )
    if public_net is not None:
        kwargs["public_net"] = public_net
    resp = client.servers.create(**kwargs)
    resp.action.wait_until_finished()
    server_id = resp.server.id
    if server_id is None:
        raise RuntimeError("Hetzner server creation returned no server ID")
    server = client.servers.get_by_id(server_id)
    return server


def delete_server(client: Client, server_id: int):
    client.servers.delete(client.servers.get_by_id(server_id))
    logging.info("Server is being deleted.")


def terminate(client: Client, server_id: int):
    logging.info("Terminating server...")
    delete_server(client, server_id)
    sys.exit(0)


quit = False


def handle_interrupted_launch():
    logging.info("Will terminate server immediately after launch. Please wait a few more seconds...")
    global quit
    quit = True


def server_to_dict(server) -> dict:
    result = {
        "id": server.id,
        "name": server.name,
        "status": server.status,
        "created": server.created,
        "server_type": {
            "id": server.server_type.id if server.server_type else None,
            "name": server.server_type.name if server.server_type else None,
        },
        "image": {
            "id": server.image.id if server.image else None,
            "name": server.image.name if server.image else None,
            "os_flavor": server.image.os_flavor if server.image else None,
            "os_version": server.image.os_version if server.image else None,
        },
        "location": {
            "id": server.location.id if server.location else None,
            "name": server.location.name if server.location else None,
        },
        "datacenter": {
            "id": server.datacenter.id if server.datacenter else None,
            "name": server.datacenter.name if server.datacenter else None,
        },
        "public_net": {},
        "primary_disk_size": server.primary_disk_size,
        "labels": server.labels,
    }
    if server.public_net:
        ipv4 = server.public_net.ipv4
        ipv6 = server.public_net.ipv6
        result["public_net"] = {
            "ipv4": {"ip": ipv4.ip, "blocked": ipv4.blocked} if ipv4 else None,
            "ipv6": {"ip": ipv6.ip, "blocked": ipv6.blocked} if ipv6 else None,
        }
    return result


def build_locations_help() -> str:
    parts = []
    for code, info in LOCATIONS.items():
        parts.append("%s (%s, %s)" % (code, info["city"], info["country"]))
    return ", ".join(parts)


def main(*, unified_output=False):
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    import argparse

    arg_parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Quickly launch a Hetzner Cloud server for small tasks. The server's "
        "lifecycle is tied to the process,\nenabling easy Ctrl+C server termination when done.",
        epilog="help & support:\n  https://github.com/personalcomputer/ec2instance/issues",
    )
    arg_parser.add_argument(
        "-t",
        "--type",
        type=str,
        default=DEFAULT_SERVER_TYPE,
        dest="server_type",
        help="Hetzner Cloud server type name (e.g. cx23, cax21, cpx31). Use the `list-types` subcommand to see all. (default: %s)"
        % DEFAULT_SERVER_TYPE,
    )
    arg_parser.add_argument(
        "-i",
        "--image",
        type=str,
        default=DEFAULT_IMAGE,
        dest="image_identifier",
        help='Hetzner Cloud image name. You may also pass "ubuntu", "debian", or "fedora" as a '
        "shortcut to get the latest system image. You can also pass an exact image name like "
        "'ubuntu-24.04'. (default: %s)" % DEFAULT_IMAGE,
    )
    arg_parser.add_argument(
        "-l",
        "--location",
        type=str,
        default=DEFAULT_LOCATION,
        dest="location",
        help="Hetzner Cloud location: %s (default: %s)" % (build_locations_help(), DEFAULT_LOCATION),
    )
    arg_parser.add_argument(
        "-f",
        "--user-data",
        type=str,
        default=DEFAULT_USER_DATA_PATH,
        dest="user_data_filename",
        help='Cloud-init "user data" script. Path to a shell script. Hetzner Cloud will run this '
        "script on the server immediately after launch. "
        "(default: %s)" % path_collapseuser(DEFAULT_USER_DATA_PATH),
    )
    arg_parser.add_argument(
        "--provisioning-script",
        "--prov",
        help="Local provisioning script name from ~/.config/ec2instance_cmd/provision_scripts/. "
        "Runs after SSH becomes available.",
    )
    arg_parser.add_argument(
        "--token",
        type=str,
        default=None,
        dest="api_token",
        help="Hetzner Cloud API token. Alternatively, set HCLOUD_TOKEN env var, or configure ~/.config/hcloud/cli.toml.",
    )
    arg_parser.add_argument(
        "--context",
        type=str,
        default=None,
        dest="context",
        help="Hetzner Cloud CLI context name (from ~/.config/hcloud/cli.toml). Used to select which token to use.",
    )
    arg_parser.add_argument(
        "-d",
        "--detach",
        "--non-interactive",
        "--json",
        action="store_true",
        dest="detach",
        help="By default an interactive shell will be opened in the spawned server, and the "
        "server will be terminated when the shell is closed. To instead "
        "output server metadata as json and then detach, specify --detach.",
    )
    arg_parser.add_argument(
        "--list-locations",
        action="store_true",
        help="List all available Hetzner Cloud locations.",
    )
    arg_parser.add_argument(
        "--ipv6-only",
        action="store_true",
        help="Do not provision a public IPv4 address. The server will only have IPv6.",
    )
    arg_parser.add_argument(
        "--show-data-path",
        action="store_true",
        help="Print out the shared ec2instance local data and configuration path.",
    )

    # `list-types` subcommand: list all server types with pricing, optionally
    # filtered by location.
    subparsers = arg_parser.add_subparsers(dest="command")
    lt = subparsers.add_parser(
        "list-types",
        help="List all server types with pricing.",
        description="List all server types with pricing. Optionally filter by location "
        "(e.g. `hetznerinstance list-types ash`).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    lt.add_argument(
        "location",
        nargs="?",
        default=None,
        metavar="LOCATION",
        help="Optional location to filter by (e.g. ash, fsn1). Omit to list all.",
    )
    subparsers.add_parser(
        "list-scripts",
        help="List and validate shared provisioning and user-data scripts.",
    )

    args = arg_parser.parse_args()

    if args.show_data_path:
        print(CONFIG_DIR)
        sys.exit(0)

    if args.list_locations:
        list_locations()
        sys.exit(0)

    if args.command == "list-scripts":
        if not list_and_validate_scripts():
            sys.exit(1)
        return

    if args.command == "list-types":
        loc_filter = args.location
        if loc_filter and loc_filter not in LOCATIONS:
            print(
                "Unknown location '%s'. Available: %s" % (loc_filter, ", ".join(LOCATIONS.keys())),
                file=sys.stderr,
            )
            sys.exit(1)
        list_server_types(loc_filter)
        sys.exit(0)

    provisioning_script = None
    if args.provisioning_script:
        try:
            provisioning_script = resolve_provisioning_script(args.provisioning_script)
        except ValueError as error:
            arg_parser.error(str(error))

    user_data = load_user_data(
        args.user_data_filename,
        DEFAULT_USER_DATA_PATH,
        USER_DATA_SCRIPTS_LIBRARY_PATH,
    )

    # Hetzner Cloud client init
    # Token resolution: --token > HCLOUD_TOKEN/HETZNER_TOKEN env > hcloud cli.toml
    api_token = args.api_token or os.environ.get("HCLOUD_TOKEN") or os.environ.get("HETZNER_TOKEN")
    if not api_token:
        api_token = load_token_from_hcloud_config(context_name=args.context)
    if not api_token:
        logging.error(
            "Unable to locate Hetzner Cloud API token!\n\n"
            "To resolve, do one of the following:\n"
            " - 1.) Set the HCLOUD_TOKEN environment variable with your Hetzner Cloud API token.\n"
            " - 2.) Pass the token via --token argument.\n"
            " - 3.) Configure the hcloud CLI (hcloud context create), which stores the token in\n"
            "       ~/.config/hcloud/cli.toml.\n"
            " - 4.) Get an API token from: https://console.hetzner.cloud/projects (Settings > API Tokens)\n"
        )
        sys.exit(1)

    client = Client(token=api_token)

    # Verify the token works by making a simple API call
    try:
        client.servers.get_list(per_page=1)
    except Exception as e:
        logging.error("Failed to authenticate with Hetzner Cloud API: %s" % str(e))
        sys.exit(1)

    # Determine architecture from server type name
    arch = get_arch_from_name(args.server_type)

    # Resolve image
    image = get_image(client, args.image_identifier, arch)
    logging.info("Using image: %s (id: %s)" % (image.name, image.id))

    # Resolve server type
    server_type = client.server_types.get_by_name(args.server_type)
    if server_type is None:
        logging.error(
            "Server type '%s' not found! Use the `list-types` subcommand to see available types." % args.server_type
        )
        sys.exit(1)

    # Resolve location
    location = client.locations.get_by_name(args.location)
    if location is None:
        logging.error("Location '%s' not found! Available: %s" % (args.location, ", ".join(LOCATIONS.keys())))
        sys.exit(1)

    # Show pricing estimate at launch
    server_types = load_server_types()
    if args.server_type in server_types:
        st_info = server_types[args.server_type]
        if args.location in st_info["locations"]:
            price = st_info["locations"][args.location]
            ipv4_note = " + €%.2f/mo IPv4" % PRIMARY_IPV4_MONTHLY_EUR if not args.ipv6_only else ""
            logging.info(
                "Server type %s (%s) at %s: €%.2f/mo (€%.4f/hr)%s"
                % (
                    args.server_type,
                    st_info["desc"],
                    args.location,
                    price["monthly"],
                    price["hourly"],
                    ipv4_note,
                )
            )
        else:
            logging.warning(
                "Server type '%s' may not be available at location '%s'. Available locations: %s"
                % (
                    args.server_type,
                    args.location,
                    ", ".join(st_info["locations"].keys()),
                )
            )

    # Check/provision prerequisites
    firewall = get_or_create_firewall(client)
    keypair_name, key_path = get_or_create_ssh_key(client)

    # Get the SSH key object for server creation
    ssh_key = client.ssh_keys.get_by_name(keypair_name)

    # Launch
    server_name = "%s-%s-%s-%s" % (
        PROGRAM_NAME,
        slugify(HOSTNAME),
        slugify(USERNAME),
        datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d-%H%M%S"),
    )
    logging.info("Launching server... (ETA to usability: ~30 seconds)")
    signal.signal(signal.SIGINT, lambda a, b: handle_interrupted_launch())
    signal.signal(signal.SIGTERM, lambda a, b: handle_interrupted_launch())

    public_net = None
    if args.ipv6_only:
        public_net = ServerCreatePublicNetwork(enable_ipv4=False, enable_ipv6=True)

    server = launch_server(
        client=client,
        image=image,
        server_type=server_type,
        location=location,
        ssh_keys=[ssh_key],
        firewalls=[firewall],
        user_data=user_data,
        server_name=server_name,
        public_net=public_net,
    )

    server_id = server.id
    if server.public_net and server.public_net.ipv4:
        server_ip = server.public_net.ipv4.ip
    elif server.public_net and server.public_net.ipv6:
        # IPv6 networks come as CIDR (e.g. "2001:db8::/64"); use the network base address
        server_ip = server.public_net.ipv6.network
    else:
        server_ip = None

    is_ipv6 = server_ip and ":" in server_ip

    if quit:
        terminate(client, server_id)
        return

    signal.signal(signal.SIGINT, lambda a, b: terminate(client, server_id))
    signal.signal(signal.SIGTERM, lambda a, b: terminate(client, server_id))

    if not server_ip:
        logging.error(
            "Server was launched but no public IP address was assigned. Check your Hetzner Cloud project settings."
        )
        terminate(client, server_id)
        return

    ip_label = "IPv6" if is_ipv6 else "IPv4"
    logging.info(
        "Server Launched! (id: %d, %s: %s) Waiting for server to finish booting..." % (server_id, ip_label, server_ip)
    )

    ssh_login_user = guess_image_default_username(args.image_identifier)
    ssh_target = "[%s]" % server_ip if is_ipv6 else server_ip
    ssh_args = [get_ssh_bin(), "-i", key_path, "%s@%s" % (ssh_login_user, ssh_target)]
    ssh_cmd = " ".join(ssh_args)
    print(ssh_cmd, flush=True)

    wait_until_accepts_connection(ip=server_ip, port=22)
    logging.info("Server is up!")

    if provisioning_script:
        try:
            run_provisioning_script(
                provisioning_script,
                ssh_keyfile=key_path,
                port=22,
                target="%s@%s" % (ssh_login_user, ssh_target),
            )
        except (subprocess.CalledProcessError, RuntimeError) as error:
            logging.error("Provisioning script failed: %s", error)
            terminate(client, server_id)
            return

    if args.detach:
        server_data = server_to_dict(server)
        output = server_data
        if unified_output:
            output = build_instance_result(
                provider="hetzner",
                resource_id=server_id,
                status=server.status,
                host=server_ip,
                port=22,
                user=ssh_login_user,
                instance_type=server.server_type.name if server.server_type else None,
                image=server.image.name if server.image else None,
                location=server.location.name if server.location else None,
                raw=server_data,
            )
        print(dump_json_with_datetimes(output, indent=2))
        return

    # Launch Shell
    logging.info("Launching shell (running above ssh command)...")
    automatic_ssh_cmd = " ".join(ssh_args + ["-o", "StrictHostKeyChecking=no"])
    os.system(automatic_ssh_cmd)

    # After shell exits, wait for SIGTERM/SIGINT
    logging.info("Server is still running. Press CTRL+C to terminate, or the command to SSH again is: %s" % ssh_cmd)
    while True:
        signal.pause()


if __name__ == "__main__":
    main()
