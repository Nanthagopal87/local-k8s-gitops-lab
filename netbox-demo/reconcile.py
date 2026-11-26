#!/usr/bin/env python3
"""Reconcile Google Cloud subnets against the NetBox IP plan. Read-only on both sides.

Usage (with the NetBox port-forward running):
    python3 netbox-demo/reconcile.py --file netbox-demo/samples/gcp-subnets-bu1-prd.json
    python3 netbox-demo/reconcile.py --project <gcp-project-id> [--project ...]
    python3 netbox-demo/reconcile.py --file export.json --json      # machine-readable

Input is what `gcloud compute networks subnets list --format=json` returns: --project runs
that command for you (read-only), --file reads a saved export. Every subnet's primary range
and each secondary range (GKE Pods / Services) is compared with NetBox prefixes carrying
the gcp_project / gcp_vpc custom fields.

Findings:
  OK            range exists in both, and project, VPC, region and role agree
  MISMATCH      same CIDR in both, but project / VPC / region / role differ
  CONFLICT      the GCP range overlaps a planned NetBox range with a different size
  UNPLANNED     in GCP only; it falls inside the plan (free space of a container)
  OUT_OF_PLAN   in GCP only; it is outside every NetBox prefix
  NOT_BUILT     planned in NetBox (for a scanned project) but absent from GCP

Exit code: 0 when everything is OK or NOT_BUILT (plan ahead of reality is normal);
1 when any MISMATCH, CONFLICT, UNPLANNED or OUT_OF_PLAN is found (drift: for CI).
"""
import argparse
import ipaddress
import json
import subprocess
import sys

from nbapi import list_all

# NetBox roles that correspond to something GCP reports as a subnet or secondary range.
PRIMARY_ROLES = {"app-subnet", "db-subnet", "dwh-subnet", "gke-nodes", "cicd-workers",
                 "shared-services", "proxy-only", "psc-nat"}
SECONDARY_ROLES = {"gke-pods", "gke-services"}
# GCP subnet purpose -> NetBox roles it may have.
PURPOSE_ROLES = {
    "PRIVATE": PRIMARY_ROLES - {"proxy-only", "psc-nat"},
    "REGIONAL_MANAGED_PROXY": {"proxy-only"},
    "GLOBAL_MANAGED_PROXY": {"proxy-only"},
    "PRIVATE_SERVICE_CONNECT": {"psc-nat"},
}
NOT_CHECKED = "psa (allocated ranges) and serverless connectors are not subnets; not checked here"

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
src = ap.add_mutually_exclusive_group(required=True)
src.add_argument("--project", action="append", help="GCP project to read live (repeatable)")
src.add_argument("--file", action="append", help="saved `gcloud ... subnets list --format=json` export (repeatable)")
ap.add_argument("--json", action="store_true", help="print findings as JSON")
args = ap.parse_args()


def last(url):  # ".../projects/p/global/networks/vpc-x" -> "vpc-x"
    return (url or "").rstrip("/").rsplit("/", 1)[-1]


def project_of(url):
    parts = (url or "").split("/")
    return parts[parts.index("projects") + 1] if "projects" in parts else None


# ---------- GCP side ----------------------------------------------------------------------
subnets = []
if args.project:
    for p in args.project:
        out = subprocess.run(["gcloud", "compute", "networks", "subnets", "list",
                              f"--project={p}", "--format=json"],
                             check=True, capture_output=True, text=True).stdout
        subnets += json.loads(out)
else:
    for f in args.file:
        with open(f, encoding="utf-8") as fh:
            subnets += json.load(fh)

gcp = []  # one entry per range
for s in subnets:
    common = {"subnet": s["name"], "project": project_of(s.get("selfLink") or s["network"]),
              "vpc": last(s["network"]), "region": last(s["region"])}
    gcp.append({**common, "cidr": s["ipCidrRange"], "kind": "primary",
                "purpose": s.get("purpose", "PRIVATE")})
    for r in s.get("secondaryIpRanges", []):
        gcp.append({**common, "cidr": r["ipCidrRange"], "kind": f"secondary:{r['rangeName']}",
                    "purpose": "SECONDARY"})
scanned_projects = {g["project"] for g in gcp} | set(args.project or [])

# ---------- NetBox side -------------------------------------------------------------------
prefixes = list_all("ipam/prefixes/")
by_cidr = {p["prefix"]: p for p in prefixes}
leaves = [p for p in prefixes
          if p["role"] and p["role"]["slug"] in PRIMARY_ROLES | SECONDARY_ROLES]


def nb_fields(p):
    cf = p.get("custom_fields") or {}
    scope = p.get("scope") or {}
    return {"project": cf.get("gcp_project"), "vpc": cf.get("gcp_vpc"),
            "region": scope.get("slug") if p.get("scope_type") == "dcim.site" else None,
            "role": p["role"]["slug"] if p["role"] else None}


# ---------- compare -----------------------------------------------------------------------
findings = []
matched = set()
for g in gcp:
    net = ipaddress.ip_network(g["cidr"])
    where = f"{g['project']}/{g['vpc']}/{g['subnet']} ({g['kind']}, {g['region']})"
    p = by_cidr.get(g["cidr"])
    if p and p["role"] and p["role"]["slug"] in PRIMARY_ROLES | SECONDARY_ROLES:
        matched.add(p["prefix"])
        nb = nb_fields(p)
        problems = []
        for k in ("project", "vpc", "region"):
            if nb[k] != g[k]:
                problems.append(f"{k}: NetBox {nb[k]!r} vs GCP {g[k]!r}")
        allowed = SECONDARY_ROLES if g["purpose"] == "SECONDARY" else PURPOSE_ROLES.get(g["purpose"], set())
        if nb["role"] not in allowed:
            problems.append(f"role: NetBox {nb['role']!r} not valid for GCP {g['purpose']}")
        findings.append({"status": "MISMATCH" if problems else "OK", "cidr": g["cidr"],
                         "gcp": where, "netbox": p["description"], "detail": "; ".join(problems)})
        continue
    overlaps = [l for l in leaves if ipaddress.ip_network(l["prefix"]).overlaps(net)]
    if overlaps:
        for l in overlaps:
            matched.add(l["prefix"])
        findings.append({"status": "CONFLICT", "cidr": g["cidr"], "gcp": where,
                         "netbox": ", ".join(f"{l['prefix']} ({l['description']})" for l in overlaps),
                         "detail": "GCP range overlaps planned range(s) of a different size"})
        continue
    parents = [p for p in prefixes if ipaddress.ip_network(p["prefix"]).supernet_of(net)]
    if parents:
        parent = max(parents, key=lambda p: ipaddress.ip_network(p["prefix"]).prefixlen)
        findings.append({"status": "UNPLANNED", "cidr": g["cidr"], "gcp": where,
                         "netbox": f"free space in {parent['prefix']} ({parent['description']})",
                         "detail": "add it to coe.yaml or allocate it with allocate.py"})
    else:
        findings.append({"status": "OUT_OF_PLAN", "cidr": g["cidr"], "gcp": where,
                         "netbox": "-", "detail": "outside every NetBox prefix"})

for l in leaves:
    nb = nb_fields(l)
    if l["prefix"] not in matched and nb["project"] in scanned_projects:
        findings.append({"status": "NOT_BUILT", "cidr": l["prefix"],
                         "gcp": "-", "netbox": l["description"],
                         "detail": f"planned in {nb['project']}/{nb['vpc']}, not found in GCP"})

# ---------- report ------------------------------------------------------------------------
ORDER = ["CONFLICT", "MISMATCH", "OUT_OF_PLAN", "UNPLANNED", "NOT_BUILT", "OK"]
findings.sort(key=lambda f: (ORDER.index(f["status"]), ipaddress.ip_network(f["cidr"])))
drift = [f for f in findings if f["status"] in ("CONFLICT", "MISMATCH", "UNPLANNED", "OUT_OF_PLAN")]

if args.json:
    print(json.dumps({"projects": sorted(p for p in scanned_projects if p), "findings": findings}, indent=2))
else:
    print(f"Projects scanned: {', '.join(sorted(p for p in scanned_projects if p))}")
    print(f"GCP ranges: {len(gcp)} from {len(subnets)} subnets; NetBox prefixes: {len(prefixes)}")
    print(f"Note: {NOT_CHECKED}\n")
    for f in findings:
        print(f"{f['status']:<11} {f['cidr']:<16} {f['gcp'] if f['gcp'] != '-' else f['netbox']}")
        if f["status"] != "OK":
            other = f["netbox"] if f["gcp"] != "-" and f["netbox"] != "-" else ""
            if other:
                print(f"{'':<28} NetBox: {other}")
            print(f"{'':<28} {f['detail']}")
    counts = {s: sum(f["status"] == s for f in findings) for s in ORDER}
    print("\n" + ", ".join(f"{s} {n}" for s, n in counts.items() if n))
    print("RESULT: drift found" if drift else "RESULT: no drift")

sys.exit(1 if drift else 0)
