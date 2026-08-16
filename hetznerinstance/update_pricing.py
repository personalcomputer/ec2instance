#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     'hcloud'
# ]
# ///
"""
Fetch Hetzner Cloud pricing data and update pricing.json.

Usage:
    HCLOUD_TOKEN=your_token uv run update_pricing.py
    HCLOUD_TOKEN=your_token uv run update_pricing.py --dry-run

Requires HCLOUD_TOKEN or HETZNER_TOKEN env var, --token flag, or hcloud CLI config.
"""
import datetime
import json
import os
import sys
import tomllib
from typing import Any

from hcloud import Client
from hcloud.server_types import BoundServerType

XDG_CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
HCLOUD_CONFIG_DIR = os.environ.get("HCLOUD_CONFIG_DIR", os.path.join(XDG_CONFIG_HOME, "hcloud"))
HCLOUD_CONFIG_PATH = os.path.join(HCLOUD_CONFIG_DIR, "cli.toml")


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
    if context_name:
        for ctx in contexts:
            if ctx.get("name") == context_name:
                return ctx.get("token")
        return None
    active_context = config.get("active_context")
    if active_context:
        for ctx in contexts:
            if ctx.get("name") == active_context:
                return ctx.get("token")
    return contexts[0].get("token")


def fetch_data(token: str) -> tuple[dict[str, Any], list[BoundServerType]]:
    client = Client(token=token)
    server_types_page = client.server_types.get_list(per_page=50)
    server_types = server_types_page.server_types
    import urllib.request

    req = urllib.request.Request(
        "https://api.hetzner.cloud/v1/pricing",
        headers={"Authorization": "Bearer %s" % token, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        pricing_raw = json.loads(resp.read())
    return pricing_raw, server_types


def _is_deprecated_everywhere(st) -> bool:
    if not st.locations:
        return False
    return all(loc.deprecation is not None for loc in st.locations)


def _non_deprecated_location_names(st) -> set[str]:
    if not st.locations:
        return set()
    return {loc.location.name for loc in st.locations if loc.deprecation is None}


def classify_server_type(st) -> str:
    if st.cpu_type == "dedicated":
        return "dedicated"
    if getattr(st, "category", None) == "cost-optimized":
        return "cost-optimized"
    if st.cpu_type == "shared":
        all_location_codes = {"fsn1", "nbg1", "hel1", "ash", "hil", "sin"}
        if _non_deprecated_location_names(st) < all_location_codes:
            return "cost-optimized"
        return "regular"
    return "regular"


def _has_all_locations(st) -> bool:
    all_location_codes = {"fsn1", "nbg1", "hel1", "ash", "hil", "sin"}
    return _non_deprecated_location_names(st) >= all_location_codes


def build_description(st) -> str:
    cpu_label = "vCPU"
    if st.cpu_type == "dedicated":
        cpu_label = "vCPU (ded.)"
    elif st.architecture == "arm":
        cpu_label = "vCPU (ARM)"
    mem = st.memory
    if mem == int(mem):
        mem = int(mem)
    return "%d %s, %sGB RAM, %dGB SSD" % (st.cores, cpu_label, mem, st.disk)


def generate_server_types_dict(server_types: list) -> dict:
    result = {}
    for st in server_types:
        if _is_deprecated_everywhere(st):
            continue
        cat = classify_server_type(st)
        desc = build_description(st)
        loc_prices = {}
        if st.prices:
            for p in st.prices:
                loc = p.get("location")
                if loc:
                    monthly = float(p.get("price_monthly", {}).get("net", "0"))
                    hourly = float(p.get("price_hourly", {}).get("net", "0"))
                    loc_prices[loc] = {"monthly": round(monthly, 2), "hourly": round(hourly, 4)}
        if not loc_prices:
            continue
        result[st.name] = {"category": cat, "desc": desc, "locations": loc_prices}
    return result


def main():
    import argparse

    arg_parser = argparse.ArgumentParser(description="Fetch Hetzner Cloud pricing and update pricing.json")
    arg_parser.add_argument("--token", type=str, default=None, help="Hetzner Cloud API token")
    arg_parser.add_argument(
        "--context",
        type=str,
        default=None,
        help="Hetzner Cloud CLI context name (from ~/.config/hcloud/cli.toml). Used to select which token to use.",
    )
    arg_parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    arg_parser.add_argument(
        "--pricing-json",
        type=str,
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "pricing.json"),
        help="Path to pricing.json",
    )
    args = arg_parser.parse_args()

    token = (
        args.token
        or os.environ.get("HCLOUD_TOKEN")
        or os.environ.get("HETZNER_TOKEN")
        or load_token_from_hcloud_config(context_name=args.context)
    )
    if not token:
        print(
            "ERROR: Unable to locate Hetzner Cloud API token!\n\n"
            "To resolve, do one of the following:\n"
            " - 1.) Set the HCLOUD_TOKEN environment variable.\n"
            " - 2.) Pass the token via --token argument.\n"
            " - 3.) Configure the hcloud CLI (hcloud context create).\n",
            file=sys.stderr,
        )
        sys.exit(1)

    now = datetime.datetime.now(tz=datetime.timezone.utc)
    date_str = now.strftime("%Y-%m")

    print("Fetching pricing data from Hetzner Cloud API...", file=sys.stderr)
    pricing_raw, server_types = fetch_data(token)

    server_types_dict = generate_server_types_dict(server_types)
    print("Found %d non-deprecated server types" % len(server_types_dict), file=sys.stderr)

    output = {
        "pricing_date": date_str,
        "server_types": server_types_dict,
        "raw": pricing_raw,
    }

    if args.dry_run:
        print(
            json.dumps(
                {"pricing_date": output["pricing_date"], "server_types_count": len(output["server_types"])}, indent=2
            )
        )
        print("[dry-run] Would write to %s" % args.pricing_json, file=sys.stderr)
    else:
        with open(args.pricing_json, "w") as f:
            json.dump(output, f, indent=2)
        print(
            "Updated %s (pricing_date=%s, %d server types)" % (args.pricing_json, date_str, len(server_types_dict)),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
