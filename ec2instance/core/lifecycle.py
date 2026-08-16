"""Provider-independent pieces of the launch/connect/destroy lifecycle."""

import datetime
import json
import os
import socket
import time
from pathlib import Path

DEFAULT_USER_DATA = """#!/bin/bash
date

# Disable MOTD
for userdir in /home/*; do touch $userdir/.hushlogin; done

# Pull latest package repository metadata
if grep -qi "Ubuntu" /etc/issue; then
    apt update -y
fi
"""


def build_instance_result(
    *,
    provider: str,
    resource_id,
    status: str | None,
    host: str | None,
    port: int | None,
    user: str | None,
    instance_type: str | None,
    image: str | None,
    location: str | None,
    raw: dict,
) -> dict:
    """Build the stable detached-output envelope used by the unified CLI."""
    return {
        "provider": provider,
        "id": resource_id,
        "status": status,
        "ssh": {"host": host, "port": port, "user": user},
        "type": instance_type,
        "image": image,
        "location": location,
        "raw": raw,
    }


def _json_object_serializer(obj):
    if hasattr(obj, "isoformat"):
        if obj.tzinfo is None:
            raise ValueError("datetime values in detached JSON must include a timezone")
        return obj.astimezone(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return json.JSONEncoder.default(obj)


def dump_json_with_datetimes(obj, **kwargs):
    return json.dumps(obj, default=_json_object_serializer, **kwargs)


def path_collapseuser(path: str) -> str:
    """Return a display path with the current home directory replaced by ``~``."""
    return path.replace(os.path.expanduser("~"), "~", 1)


def load_user_data(requested_path: str, default_path: str, library_path: str) -> str:
    """Create the default script if needed, resolve a script path, and read it."""
    default = Path(default_path)
    if not default.exists():
        default.parent.mkdir(parents=True, exist_ok=True)
        default.write_text(DEFAULT_USER_DATA)

    requested = Path(requested_path)
    library_candidate = Path(library_path) / requested_path
    if requested.exists():
        selected = requested
    elif library_candidate.exists():
        selected = library_candidate
    else:
        raise ValueError("Cannot open %s" % requested_path)
    return selected.read_text()


def wait_until_accepts_connection(ip: str, port: int, polling_interval_s: float = 1.5):
    """Wait indefinitely for a TCP endpoint while a provider launch is completing."""
    while True:
        try:
            connection = socket.create_connection((ip, port), timeout=polling_interval_s)
        except (ConnectionRefusedError, ConnectionResetError, TimeoutError, socket.gaierror):
            time.sleep(polling_interval_s)
        except socket.timeout:
            pass
        else:
            connection.close()
            return
