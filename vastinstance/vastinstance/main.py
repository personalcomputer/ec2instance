#!/usr/bin/env python3
import argparse
import datetime
import json
import logging
import os
import signal
import socket
import sys
import time
import unicodedata

from vastai import VastAI

from vastinstance import offers

PROGRAM_NAME = "vastinstance"
HOSTNAME = socket.gethostname()
USERNAME = os.environ.get("USER", "")
XDG_CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
CONFIG_DIR = os.path.join(XDG_CONFIG_HOME, PROGRAM_NAME)

IMAGE_TEMPLATES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image_templates.json")

DEFAULT_AMI = "nvidia-cuda"
DEFAULT_SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519")

_TEMPLATES = None


def _load_templates() -> dict:
    global _TEMPLATES
    if _TEMPLATES is None:
        with open(IMAGE_TEMPLATES_PATH) as f:
            _TEMPLATES = json.load(f)
    return _TEMPLATES


def dump_json_with_datetimes(obj, **kwargs):
    return json.dumps(obj, default=_json_object_serializer, **kwargs)


def _json_object_serializer(obj):
    if hasattr(obj, "isoformat"):
        assert obj.tzinfo is not None
        return obj.astimezone(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return json.JSONEncoder.default(obj)


def slugify(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    value = value.strip().lower()
    return value


def path_collapseuser(path):
    return path.replace(os.path.expanduser("~"), "~", 1)


def get_ssh_bin():
    return "ssh"


def smart_split(s, char):
    in_double_quotes = False
    in_single_quotes = False
    parts = []
    current = []
    for c in s:
        if c == char and not (in_double_quotes or in_single_quotes):
            parts.append("".join(current))
            current = []
        elif c == "'":
            in_single_quotes = not in_single_quotes
            current.append(c)
        elif c == '"':
            in_double_quotes = not in_double_quotes
            current.append(c)
        else:
            current.append(c)
    parts.append("".join(current))
    return parts


def parse_env(envs):
    result = {}
    if envs is None:
        return result
    env = smart_split(envs, " ")
    prev = None
    for e in env:
        if prev is None:
            if e in {"-e", "-p", "-h", "-v", "-n"}:
                prev = e
        else:
            if prev == "-p":
                if set(e).issubset(set("0123456789:tcp/udp")):
                    result["-p " + e] = "1"
            elif prev == "-e":
                kv = e.split("=")
                if len(kv) >= 2:
                    val = kv[1]
                    if len(kv) > 2:
                        val = "=".join(kv[1:])
                    result[kv[0]] = val.strip("'\"")
            elif prev == "-v":
                if set(e).issubset(set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:./_")):
                    result["-v " + e] = "1"
            elif prev == "-n":
                if set(e).issubset(set("abcdefghijklmnopqrstuvwxyz0123456789-")):
                    result["-n " + e] = "1"
            else:
                result[prev] = e
            prev = None
    return result


def compute_runtype(jupyter, ssh, direct):
    if jupyter:
        return "jupyter_direc ssh_direc ssh_proxy" if direct else "jupyter_proxy ssh_proxy"
    elif ssh:
        return "ssh_direc ssh_proxy" if direct else "ssh_proxy"
    return None


def resolve_template(name):
    templates = _load_templates()
    if name not in templates:
        raise ValueError(
            "Unknown image template '%s'. Available: %s"
            % (name, ", ".join(sorted(templates.keys())))
        )
    return templates[name]


def get_ssh_key_path():
    candidates = [
        DEFAULT_SSH_KEY_PATH,
        os.path.expanduser("~/.ssh/id_rsa"),
        os.path.expanduser("~/.ssh/id_ecdsa"),
        os.path.expanduser("~/.ssh/id_ed25519_sk"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def launch_instance(vast, offer_id, template):
    env = parse_env(template.get("env"))
    runtype = compute_runtype(
        jupyter=template.get("jupyter", False),
        ssh=template.get("ssh", True),
        direct=template.get("direct", True),
    )
    kwargs = {
        "id": offer_id,
        "image": template["image"],
        "disk": template["disk"],
        "env": env,
        "runtype": runtype,
    }
    if template.get("onstart_cmd"):
        kwargs["onstart_cmd"] = template["onstart_cmd"]
    return vast.create_instance(**kwargs)


def wait_until_accepts_connection(ip, port):
    POLLING_INTERVAL_S = 1.5
    while True:
        try:
            s = socket.create_connection((ip, port), timeout=POLLING_INTERVAL_S)
        except (ConnectionRefusedError, ConnectionResetError):
            time.sleep(POLLING_INTERVAL_S)
        except TimeoutError:
            pass
        except socket.gaierror:
            time.sleep(POLLING_INTERVAL_S)
        else:
            s.close()
            return


def instance_to_dict(inst):
    if not isinstance(inst, dict):
        return {}
    keys = [
        "id",
        "actual_status",
        "label",
        "image",
        "image_uuid",
        "image_runtype",
        "machine_id",
        "template_name",
        "num_gpus",
        "gpu_name",
        "dph_total",
        "disk_space",
        "ssh_host",
        "ssh_port",
        "public_ipaddr",
        "ports",
        "start_date",
        "geolocation",
        "status_msg",
    ]
    return {k: inst.get(k) for k in keys if k in inst}


def resolve_ssh_endpoint(inst: dict) -> tuple[str | None, int | None]:
    # Resolve (ip, port) for SSHing into a vast.ai instance. Prefer a direct
    # connection to the host's public IP on the mapped host port for container
    # port 22/tcp (what `vastai ssh-url` uses for --direct instances); fall back
    # to the ssh proxy (ssh_host/ssh_port) when no direct port mapping exists.
    ports = inst.get("ports") or {}
    port_22 = ports.get("22/tcp")
    if port_22:
        host_port = None
        try:
            host_port = int(port_22[0].get("HostPort"))
        except (TypeError, ValueError, IndexError):
            host_port = None
        ipaddr = inst.get("public_ipaddr")
        if ipaddr and host_port:
            return ipaddr, host_port
    ssh_host = inst.get("ssh_host")
    ssh_port = inst.get("ssh_port")
    if ssh_host and ssh_port:
        return ssh_host, int(ssh_port)
    return None, None


def destroy_instance(vast, instance_id):
    vast.destroy_instance(id=instance_id)
    logging.info("Instance is being destroyed.")


def terminate(vast, instance_id):
    logging.info("Destroying instance...")
    destroy_instance(vast, instance_id)
    sys.exit(0)


quit = False


def handle_interrupted_launch():
    logging.info("Will destroy instance immediately after launch. Please wait a few more seconds...")
    global quit
    quit = True


def list_templates():
    templates = _load_templates()
    print()
    print("  %-24s  %-8s  %s" % ("Template", "Disk", "Description"))
    print("  %-24s  %-8s  %s" % ("--------", "----", "-----------"))
    for name in sorted(templates.keys()):
        info = templates[name]
        print("  %-24s  %-8s  %s" % (name, "%dGB" % info["disk"], info.get("description", "")))
    print()


def main():
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")

    templates = _load_templates()
    template_names = sorted(templates.keys())

    arg_parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Quickly launch a vast.ai instance for small tasks. The instance's "
        "lifecycle is tied to the process,\nenabling easy Ctrl+C instance destruction when done.",
        epilog="help & support:\n  https://github.com/personalcomputer/ec2instance/issues",
    )
    arg_parser.add_argument(
        "-t",
        "--type",
        type=str,
        default=None,
        dest="offer_id",
        help="vast.ai offer ID (an integer returned from `vastai search offers`). "
        "Each offer ID can only be used to create one instance. (required)",
    )
    arg_parser.add_argument(
        "-i",
        "--ami",
        type=str,
        default=DEFAULT_AMI,
        dest="template",
        choices=template_names,
        help="Image template to launch. Use --list-templates to see all. (default: %s)" % DEFAULT_AMI,
    )
    arg_parser.add_argument(
        "--disk",
        type=int,
        default=None,
        dest="disk",
        help="Override the template's disk size (GiB). (default: per template)",
    )
    arg_parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        dest="api_key",
        help="vast.ai API key. Alternatively, set VAST_API_KEY env var, or save it to "
        "~/.config/vastai/vast_api_key (the file the vastai CLI uses).",
    )
    arg_parser.add_argument(
        "-d",
        "--detach",
        "--non-interactive",
        "--json",
        action="store_true",
        dest="detach",
        help="By default an interactive SSH shell will be opened in the spawned instance, and "
        "the instance will be destroyed when the shell is closed. To instead output instance "
        "metadata as json and then detach, specify --detach.",
    )
    arg_parser.add_argument(
        "--list-templates",
        action="store_true",
        help="List all available image templates.",
    )
    arg_parser.add_argument(
        "--show-data-path",
        action="store_true",
        help="Print out the path where vastinstance is storing local data and configuration.",
    )

    # `list-types` subcommand: list the top N cheapest available vast.ai offers.
    subparsers = arg_parser.add_subparsers(dest="command")
    lt = subparsers.add_parser(
        "list-types",
        help="List the top N cheapest available vast.ai offers (on-demand + interruptible).",
        description="List the top N cheapest available vast.ai offers (on-demand + interruptible). "
        "Effective price = rent + bandwidth (assumed UL/DL traffic), computed client-side.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    lt.add_argument("-n", type=int, default=100, help="number of cheapest instances to show (default: 100)")
    lt.add_argument("--query", type=str, default="rentable=true", help="vastai query string (default: rentable=true)")
    lt.add_argument(
        "--limit", type=int, default=2000,
        help="offers to fetch per type (eff-price sort is client-side, so pool should exceed n) (default: 2000)",
    )
    lt.add_argument("--up-gb-hr", type=float, default=8.0, help="assumed upload traffic in GB/hr for effective price (default: 8.0)")
    lt.add_argument("--down-gb-hr", type=float, default=4.0, help="assumed download traffic in GB/hr for effective price (default: 4.0)")
    lt.add_argument("--disk-gb", type=float, default=16.0, help="assumed disk storage in GiB for effective price (default: 16.0)")
    lt.add_argument("--vm-only", action="store_true", help="only VM-capable offers (vms_enabled=true)")
    lt.add_argument("--container-only", action="store_true", help="only container offers (vms_enabled=false)")
    lt.add_argument("--eur", action="store_true", help="only offers in Europe (EU-27 + UK, Norway, Switzerland)")
    lt.add_argument("--sec-only", action="store_true", help="only secure datacenter offers (datacenter=true)")
    lt.add_argument(
        "--no-min-spec", action="store_true",
        help="disable the default min-spec filter (compute_cap>=700, inet_up>=100, inet_down>=100 Mb/s, "
        "cpu_ram>=12 GB, cpu_cores_effective>=4)",
    )
    lt.add_argument("--latency", action="store_true", help="probe each host IP for RTT via ICMP ping (adds ~1-2s per unique IP; needs CAP_NET_RAW)")
    lt.add_argument("--on-demand-only", action="store_true", dest="od_only", help="only on-demand pricing")
    lt.add_argument("--interruptible-only", action="store_true", dest="int_only", help="only interruptible (bid) pricing")
    lt.add_argument(
        "--api-key", type=str, default=None, dest="api_key",
        help="vast.ai API key. Alternatively, set VAST_API_KEY env var, or save it to "
        "~/.config/vastai/vast_api_key (the file the vastai CLI uses).",
    )

    args = arg_parser.parse_args()

    if args.show_data_path:
        print(CONFIG_DIR)
        sys.exit(0)

    if args.list_templates:
        list_templates()
        sys.exit(0)

    if args.command == "list-types":
        vast = VastAI(api_key=args.api_key)
        offers.list_types(vast, args)
        return

    if args.offer_id is None:
        arg_parser.error("the following arguments are required: -t/--type")

    # Resolve template
    template = resolve_template(args.template)
    if args.disk is not None:
        template = {**template}
        template["disk"] = args.disk
    logging.info(
        "Using image template '%s': %s (image: %s, disk: %dGB)"
        % (args.template, template.get("description", ""), template["image"], template["disk"])
    )

    # Check for a usable local SSH private key (vast.ai injects the account's
    # registered public keys into --ssh instances; we use the matching private key).
    ssh_key_path = get_ssh_key_path()
    if ssh_key_path is None:
        logging.error(
            "No SSH private key found in ~/.ssh/. vast.ai --ssh instances require you to have a "
            "key registered with your account. Generate one (ssh-keygen -t ed25519), register its "
            ".pub with `vastai create ssh-key`, and re-run."
        )
        sys.exit(1)

    # vast.ai SDK client init. Passing api_key=None lets the SDK resolve the key
    # from VAST_API_KEY or ~/.config/vastai/vast_api_key.
    vast = VastAI(api_key=args.api_key)

    # Verify the key works with a simple API call
    try:
        vast.show_instances()
    except Exception as e:
        logging.error("Failed to authenticate with vast.ai API: %s" % str(e))
        sys.exit(1)

    # Launch
    logging.info("Launching instance... (ETA to usability: ~60 seconds)")
    signal.signal(signal.SIGINT, lambda a, b: handle_interrupted_launch())
    signal.signal(signal.SIGTERM, lambda a, b: handle_interrupted_launch())
    try:
        result = launch_instance(vast, args.offer_id, template)
    except Exception as e:
        logging.error("Failed to create instance: %s" % str(e))
        sys.exit(1)

    if not result.get("success"):
        logging.error("Failed to create instance: %s" % result.get("msg", result))
        sys.exit(1)

    instance_id = result.get("new_contract")
    if instance_id is None:
        logging.error("Instance created but no instance id returned: %s" % result)
        sys.exit(1)
    logging.info("Instance created! (id: %s)" % instance_id)

    if quit:
        terminate(vast, instance_id)
        return

    signal.signal(signal.SIGINT, lambda a, b: terminate(vast, instance_id))
    signal.signal(signal.SIGTERM, lambda a, b: terminate(vast, instance_id))

    # Poll until the instance is running and has a usable SSH endpoint
    logging.info("Waiting for instance to finish booting...")
    POLL_INTERVAL = 3
    inst = None
    ssh_host = None
    ssh_port = None
    while True:
        try:
            inst = vast.show_instance(instance_id)
        except Exception as e:
            logging.warning("Unable to fetch instance status: %s" % str(e))
            inst = None
        if inst:
            actual = inst.get("actual_status")
            if actual == "running":
                ssh_host, ssh_port = resolve_ssh_endpoint(inst)
                if ssh_host and ssh_port:
                    break
            if actual in ("error", "exited", "stopped", "paused"):
                logging.error("Instance did not come up (state: %s)." % actual)
                if args.detach:
                    print(dump_json_with_datetimes(instance_to_dict(inst), indent=2))
                terminate(vast, instance_id)
                return
        if quit:
            terminate(vast, instance_id)
            return
        time.sleep(POLL_INTERVAL)

    logging.info("Instance is up! (id: %s, %s:%s)" % (instance_id, ssh_host, ssh_port))

    ssh_login_user = "root"
    ssh_args = [get_ssh_bin(), "-i", ssh_key_path, "-p", str(ssh_port), "%s@%s" % (ssh_login_user, ssh_host)]
    ssh_cmd = " ".join(ssh_args)
    print(ssh_cmd)

    wait_until_accepts_connection(ip=ssh_host, port=int(ssh_port))

    if args.detach:
        print(dump_json_with_datetimes(instance_to_dict(inst), indent=2))
        return

    # Launch Shell
    logging.info("Launching shell (running above ssh command)...")
    automatic_ssh_cmd = " ".join(ssh_args + ["-o", "StrictHostKeyChecking=no"])
    os.system(automatic_ssh_cmd)

    # After shell exits, wait for SIGTERM/SIGINT
    logging.info(
        "Instance is still running. Press CTRL+C to destroy, or the command to SSH again is: %s" % ssh_cmd
    )
    while True:
        signal.pause()


if __name__ == "__main__":
    main()