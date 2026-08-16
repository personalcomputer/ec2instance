"""List the top N cheapest available vast.ai offers (on-demand + interruptible).

Ported from the standalone vastai_top100.py script. Uses the vastai SDK
(VastAI.search_offers) instead of shelling out to the `vastai` CLI.
"""
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.style import Style
from rich.table import Table
from rich.text import Text
from vastai import VastAI

BAD = Style(bgcolor="#822828")

# EU-27 + UK, Norway, Switzerland (vast.ai geolocation codes are ISO country codes).
EU_CODES = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "GB", "NO", "CH",
]

DEFAULT_MIN_SPEC = "compute_cap>=700 inet_up>=100 inet_down>=100 cpu_ram>=12 cpu_cores_effective>=4"


def cell(val: str, bad: bool) -> object:
    return Text(val, style=BAD) if bad else val


def probe_latency(ip: str, count: int = 3, timeout: float = 2.0) -> float | None:
    # Return mean RTT (ms) to the target via ICMP ping, or None if unreachable.
    # Requires the `ping` binary and CAP_NET_RAW (raw ICMP sockets). Returns None
    # on permission errors so callers can degrade gracefully.
    if not shutil.which("ping"):
        return None
    try:
        proc = subprocess.run(
            ["ping", "-c", str(count), "-W", str(int(timeout)), ip],
            capture_output=True, text=True, timeout=count * timeout + 5, check=False,
        )
    except (subprocess.TimeoutExpired, PermissionError, OSError):
        return None
    times = [float(m) for m in re.findall(r"time=([0-9.]+)\s*ms", proc.stdout)]
    if not times:
        return None
    return sum(times) / len(times)


def probe_latencies(rows: list[dict], workers: int = 32) -> dict[str, float | None]:
    # Probe each unique public_ipaddr once (concurrently), return {ip: rtt_ms or None}.
    ips = []
    seen = set()
    for r in rows:
        ip = r.get("public_ipaddr")
        if ip and ip not in seen:
            seen.add(ip)
            ips.append(ip)
    cache: dict[str, float | None] = {}
    done = 0
    lock = threading.Lock()

    def work(ip: str) -> tuple[str, float | None]:
        return ip, probe_latency(ip)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, ip): ip for ip in ips}
        for fut in as_completed(futures):
            ip, rtt = fut.result()
            cache[ip] = rtt
            with lock:
                done += 1
                sys.stderr.write(f"\rprobing latency {done}/{len(ips)}...\x1b[K")
                sys.stderr.flush()
    sys.stderr.write("\r\x1b[K")
    return cache


def _gb(mib: float) -> float:
    return (mib or 0) / 1024.0


def _type_tag(r: dict) -> str:
    base = "OD" if r["type"] == "on-demand" else "Int"
    tags = []
    if r.get("hosting_type") == 1:
        tags.append("+Sec")
    if r.get("vms_enabled"):
        tags.append("+VM")
    return base + (" " + " ".join(tags) if tags else "")


def _build_query(
    query: str, vm_only: bool, container_only: bool, eur: bool, sec_only: bool, no_min_spec: bool
) -> str:
    if vm_only and container_only:
        raise ValueError("--vm-only and --container-only are mutually exclusive")
    parts = [p for p in query.split() if p]
    if vm_only:
        parts.append("vms_enabled=True")
    elif container_only:
        parts.append("vms_enabled=False")
    if eur:
        parts.append("geolocation in [" + ",".join(EU_CODES) + "]")
    if sec_only:
        parts.append("datacenter=true")
    if not no_min_spec:
        parts.append(DEFAULT_MIN_SPEC)
    return " ".join(parts)


def _fetch(vast: VastAI, query: str, offer_type: str, order: str, limit: int, storage_gb: float) -> list[dict]:
    return vast.search_offers(
        query=query, type=offer_type, order=order, limit=limit, storage=storage_gb, no_default=True
    )


def _build_table(
    rows: list[dict], n: int, title: str, up_gb_hr: float, down_gb_hr: float, disk_gb: float,
    latencies: dict[str, float | None] | None,
) -> Table:
    cap = (
        f"eff $/4hr = 4 * (rent (incl {disk_gb:g} GiB disk) + {up_gb_hr:g} GB/hr UL + {down_gb_hr:g} GB/hr DL), "
        "rounded to cent; speed in Mb/s; bw prices in $/TB; RAM/VRAM in GB (effective); "
        "Type OD=on-demand Int=interruptible, +Sec=secure datacenter, +VM=VM-capable"
    )
    table = Table(title=title, caption=cap)
    cols = [
        ("ID", "left"), ("Type", "left"), ("Location", "left"), ("eff $/4hr", "right"),
        ("GPU", "left"), ("CPU", "left"),
        ("UL Mb/s", "right"), ("DL Mb/s", "right"),
        ("lat ms", "right"),
        ("rent $/hr", "right"), ("bw $/hr", "right"), ("UL $/TB", "right"), ("DL $/TB", "right"),
    ]
    for c, just in cols:
        table.add_column(c, justify=just, no_wrap=True, overflow="ellipsis")
    for r in rows[:n]:
        n_gpus = r["num_gpus"]
        vram_tot = _gb(r.get("gpu_total_ram"))
        gpu = f"{n_gpus}x {r['gpu_name']} {round(vram_tot)}gb (tot) (S{r.get('dlperf', 0):.0f})"
        vcpu = r.get("cpu_cores_effective") or 0
        vcpu_s = f"{vcpu:g}" if vcpu == int(vcpu) else f"{vcpu:.1f}"
        ghz = r.get("cpu_ghz") or 0.0
        ram = _gb(r.get("cpu_ram"))
        cpu = f"{vcpu_s}x {round(ghz)}ghz {round(ram)}gb"
        ul = r.get("inet_up") or 0
        dl = r.get("inet_down") or 0
        ul_cost = r.get("internet_up_cost_per_tb") or 0.0
        dl_cost = r.get("internet_down_cost_per_tb") or 0.0
        loc = r.get("geolocation") or "?"
        lat_s = "-"
        if latencies is not None:
            v = latencies.get(r.get("public_ipaddr"))
            lat_s = f"{v:.0f}" if v is not None else "?"
        table.add_row(
            str(r["id"]),
            _type_tag(r),
            loc,
            f'${4 * r["eff"]:.2f}',
            cell(gpu, (r.get("compute_cap") or 9999) < 700),
            cell(cpu, vcpu < 2),
            cell(f"{ul:.0f}", ul < 100),
            cell(f"{dl:.0f}", dl < 100),
            lat_s,
            f'{r["rent"]:.4f}',
            f'{r["bw"]:.4f}',
            f"{ul_cost:.2f}",
            f"{dl_cost:.2f}",
        )
    return table


def list_types(vast: VastAI, args) -> None:
    """Print a table of the top N cheapest vast.ai offers (on-demand + interruptible)."""
    query = _build_query(
        args.query, args.vm_only, args.container_only, args.eur, args.sec_only, args.no_min_spec
    )
    limit = args.limit if args.limit > 0 else 2000
    rows: list[dict] = []
    if not args.int_only:
        for o in _fetch(vast, query, "on-demand", "dph_total", limit, args.disk_gb):
            o["type"] = "on-demand"
            o["rent"] = o["dph_total"]
            o["bw"] = args.up_gb_hr * (o.get("inet_up_cost") or 0.0) + args.down_gb_hr * (o.get("inet_down_cost") or 0.0)
            o["eff"] = o["rent"] + o["bw"]
            rows.append(o)
    if not args.od_only:
        for o in _fetch(vast, query, "bid", "min_bid", limit, args.disk_gb):
            o["type"] = "interrupt"
            o["rent"] = o["min_bid"]
            o["bw"] = args.up_gb_hr * (o.get("inet_up_cost") or 0.0) + args.down_gb_hr * (o.get("inet_down_cost") or 0.0)
            o["eff"] = o["rent"] + o["bw"]
            rows.append(o)
    rows.sort(key=lambda r: r["eff"])
    latencies = probe_latencies(rows[:args.n]) if args.latency else None
    console = Console(width=240, force_terminal=True, color_system="truecolor")
    console.print(
        _build_table(rows, args.n, f"Top {args.n} cheapest available vast.ai offers", args.up_gb_hr, args.down_gb_hr, args.disk_gb, latencies)
    )
    console.print(
        f"[dim]scanned: {len(rows)} offers | eff $/4hr = 4 * (rent + {args.up_gb_hr:g} GB/hr UL + "
        f"{args.down_gb_hr:g} GB/hr DL), {args.disk_gb:g} GiB disk[/dim]"
    )
