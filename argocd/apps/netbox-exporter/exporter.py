"""NetBox IPAM exporter for Prometheus (standard library only). See ADR-009.

Serves /metrics (Prometheus text format) and /healthz on :9105. Each scrape reuses the last
NetBox snapshot if it is younger than CACHE_SECONDS, so Prometheus never drives more than
one round of API calls per minute.

Two ratios per prefix:
  * coe_prefix_utilization_ratio: NetBox's own rule. Containers and aggregates count child
    prefixes (union, so nesting isn't double-counted); other prefixes count IP addresses
    and IP ranges. A VPC whose two /17 region blocks exist therefore reads 100%.
  * coe_prefix_assigned_ratio: share covered by real subnets only (non-container
    descendants). This is the meaningful "how full" number for VPCs and aggregates.

Environment:
  NETBOX_URL     e.g. http://netbox.netbox.svc.cluster.local
  NETBOX_AUTH    the full Authorization header value ("Bearer nbt_...")
  CACHE_SECONDS  default 60
"""
import ipaddress
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

URL = os.environ["NETBOX_URL"].rstrip("/")
AUTH = os.environ["NETBOX_AUTH"]
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "60"))
PORT = 9105

_lock = threading.Lock()
_cache = {"at": 0.0, "body": "", "ok": False, "last_success": 0.0, "duration": 0.0}


def fetch_all(path):
    out, url = [], f"{URL}/api/{path}?limit=500"
    while url:
        req = urllib.request.Request(url, headers={"Authorization": AUTH, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        out += data["results"]
        url = data.get("next")
    return out


def esc(v):
    return str(v if v is not None else "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def covered(net, children):
    """Addresses of `net` covered by the union of `children` networks."""
    inside = [c for c in children if c != net and c.subnet_of(net)]
    return sum(n.num_addresses for n in ipaddress.collapse_addresses(inside))


def collect():
    prefixes = fetch_all("ipam/prefixes/")
    aggregates = fetch_all("ipam/aggregates/")
    ip_addresses = fetch_all("ipam/ip-addresses/")
    ip_ranges = fetch_all("ipam/ip-ranges/")

    nets = [ipaddress.ip_network(p["prefix"]) for p in prefixes]
    leaf_nets = [n for p, n in zip(prefixes, nets) if p["status"]["value"] != "container"]
    addrs = [ipaddress.ip_interface(a["address"]).ip for a in ip_addresses]
    ranges = [(ipaddress.ip_address(r["start_address"].split("/")[0]),
               ipaddress.ip_address(r["end_address"].split("/")[0])) for r in ip_ranges]

    lines = [
        "# HELP coe_prefix_utilization_ratio Share of the prefix in use (NetBox rule), 0-1.",
        "# TYPE coe_prefix_utilization_ratio gauge",
    ]
    size_lines = ["# HELP coe_prefix_size_addresses Addresses in the prefix.",
                  "# TYPE coe_prefix_size_addresses gauge"]
    used_lines = ["# HELP coe_prefix_used_addresses Addresses in use (child prefixes, or IPs/ranges).",
                  "# TYPE coe_prefix_used_addresses gauge"]
    assigned_lines = ["# HELP coe_prefix_assigned_ratio Share of the prefix covered by real (non-container) subnets, 0-1.",
                      "# TYPE coe_prefix_assigned_ratio gauge"]
    counts = {}
    for p, net in zip(prefixes, nets):
        status = p["status"]["value"]
        role = p["role"]["slug"] if p["role"] else ""
        tenant = p["tenant"]["slug"] if p["tenant"] else ""
        env = next((t["slug"][4:] for t in p["tags"] if t["slug"].startswith("env-")), "")
        site = (p.get("scope") or {}).get("slug", "") if p.get("scope_type") == "dcim.site" else ""
        cf = p.get("custom_fields") or {}
        if status == "container":
            used = covered(net, nets)
        else:
            used = sum(1 for a in addrs if a in net)
            used += sum(int(e) - int(s) + 1 for s, e in ranges if s in net)
        ratio = used / net.num_addresses
        labels = (f'prefix="{esc(p["prefix"])}",tenant="{esc(tenant)}",env="{esc(env)}",'
                  f'site="{esc(site)}",role="{esc(role)}",status="{esc(status)}",'
                  f'vpc="{esc(cf.get("gcp_vpc"))}",project="{esc(cf.get("gcp_project"))}"')
        lines.append(f"coe_prefix_utilization_ratio{{{labels}}} {ratio:.6f}")
        size_lines.append(f"coe_prefix_size_addresses{{{labels}}} {net.num_addresses}")
        used_lines.append(f"coe_prefix_used_addresses{{{labels}}} {used}")
        if status == "container":
            assigned_lines.append(f"coe_prefix_assigned_ratio{{{labels}}} "
                                  f"{covered(net, leaf_nets) / net.num_addresses:.6f}")
        key = (tenant, env, role, status)
        counts[key] = counts.get(key, 0) + 1

    agg_lines = ["# HELP coe_aggregate_utilization_ratio Share of the aggregate covered by prefixes, 0-1.",
                 "# TYPE coe_aggregate_utilization_ratio gauge",
                 "# HELP coe_aggregate_assigned_ratio Share of the aggregate covered by real subnets, 0-1.",
                 "# TYPE coe_aggregate_assigned_ratio gauge"]
    for a in aggregates:
        net = ipaddress.ip_network(a["prefix"])
        ratio = covered(net, nets) / net.num_addresses
        agg_lines.append(f'coe_aggregate_utilization_ratio{{prefix="{esc(a["prefix"])}",'
                         f'rir="{esc(a["rir"]["slug"])}"}} {ratio:.6f}')
        agg_lines.append(f'coe_aggregate_assigned_ratio{{prefix="{esc(a["prefix"])}",'
                         f'rir="{esc(a["rir"]["slug"])}"}} {covered(net, leaf_nets) / net.num_addresses:.6f}')

    count_lines = ["# HELP coe_prefixes Number of prefixes by tenant, environment, role and status.",
                   "# TYPE coe_prefixes gauge"]
    for (tenant, env, role, status), n in sorted(counts.items()):
        count_lines.append(f'coe_prefixes{{tenant="{esc(tenant)}",env="{esc(env)}",'
                           f'role="{esc(role)}",status="{esc(status)}"}} {n}')

    other = ["# HELP coe_ip_addresses Number of IP addresses recorded in NetBox.",
             "# TYPE coe_ip_addresses gauge", f"coe_ip_addresses {len(ip_addresses)}",
             "# HELP coe_ip_ranges Number of IP ranges recorded in NetBox.",
             "# TYPE coe_ip_ranges gauge", f"coe_ip_ranges {len(ip_ranges)}"]
    return "\n".join(lines + size_lines + used_lines + assigned_lines + agg_lines + count_lines + other) + "\n"


def snapshot():
    with _lock:
        if time.time() - _cache["at"] >= CACHE_SECONDS:
            start = time.time()
            try:
                _cache["body"] = collect()
                _cache["ok"] = True
                _cache["last_success"] = time.time()
            except Exception as e:  # keep serving the last good data, report the failure
                _cache["ok"] = False
                print(f"collect failed: {e!r}", flush=True)
            _cache["duration"] = time.time() - start
            _cache["at"] = time.time()
        meta = ("# HELP coe_exporter_up 1 if the last NetBox collection succeeded.\n"
                "# TYPE coe_exporter_up gauge\n"
                f"coe_exporter_up {1 if _cache['ok'] else 0}\n"
                "# HELP coe_exporter_last_success_timestamp_seconds Time of the last good collection.\n"
                "# TYPE coe_exporter_last_success_timestamp_seconds gauge\n"
                f"coe_exporter_last_success_timestamp_seconds {_cache['last_success']:.0f}\n"
                "# HELP coe_exporter_collect_duration_seconds Duration of the last collection.\n"
                "# TYPE coe_exporter_collect_duration_seconds gauge\n"
                f"coe_exporter_collect_duration_seconds {_cache['duration']:.3f}\n")
        return _cache["body"] + meta


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            body, ctype = snapshot().encode(), "text/plain; version=0.0.4; charset=utf-8"
        elif self.path == "/healthz":
            body, ctype = b"ok\n", "text/plain"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # no per-request access log
        pass


if __name__ == "__main__":
    print(f"netbox exporter listening on :{PORT}, NetBox {URL}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
