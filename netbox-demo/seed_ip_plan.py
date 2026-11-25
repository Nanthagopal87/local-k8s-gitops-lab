#!/usr/bin/env python3
"""Seed the fictional IP plan in ip-plan.yaml into NetBox. Idempotent.

Usage (with the NetBox port-forward running):
    python3 netbox-demo/seed_ip_plan.py

Creates the RIR, aggregate, roles, tags, one /16 container per environment and its child
prefixes. Existing objects are matched (RIR/role/tag by slug, aggregate/prefix by CIDR);
for prefixes, a description/status/role/tags that differs from the YAML is corrected, so the
YAML is the desired state. Prefixes that exist only in NetBox (for example ones handed out
by allocate.py) are never touched or deleted.
"""
import pathlib

import yaml

from nbapi import call, ensure

plan = yaml.safe_load((pathlib.Path(__file__).parent / "ip-plan.yaml").read_text())
counts = {"created": 0, "updated": 0}


def report(kind, label, obj_created):
    counts["created"] += obj_created
    print(f"{'created' if obj_created else 'exists '}  {kind:<9} {label}")


def reconcile_prefix(obj, body):
    """PATCH the fields that drifted from the YAML; return True if anything changed."""
    current = {
        "description": obj["description"],
        "status": obj["status"]["value"],
        "role": obj["role"]["id"] if obj["role"] else None,
        "tags": sorted(t["slug"] for t in obj["tags"]),
    }
    wanted = {
        "description": body["description"],
        "status": body["status"],
        "role": body["role"],
        "tags": sorted(t["slug"] for t in body["tags"]),
    }
    diff = {k: v for k, v in wanted.items() if current[k] != v}
    if not diff:
        return False
    if "tags" in diff:
        diff["tags"] = body["tags"]
    status, data = call("PATCH", f"ipam/prefixes/{obj['id']}/", diff)
    if status != 200:
        raise RuntimeError(f"PATCH prefix {obj['prefix']} -> {status}: {data}")
    counts["updated"] += 1
    print(f"updated  prefix    {obj['prefix']:<16} fields: {', '.join(sorted(diff))}")
    return True


r = plan["rir"]
rir, c = ensure("ipam/rirs/", {"slug": r["slug"]}, r)
report("rir", r["name"], c)

a = plan["aggregate"]
_, c = ensure("ipam/aggregates/", {"prefix": a["prefix"]},
              {"prefix": a["prefix"], "rir": rir["id"], "description": a["description"]})
report("aggregate", a["prefix"], c)

roles = {}
for role in plan["roles"]:
    obj, c = ensure("ipam/roles/", {"slug": role["slug"]}, role)
    roles[role["slug"]] = obj["id"]
    report("role", role["name"], c)

for tag in plan["tags"]:
    _, c = ensure("extras/tags/", {"slug": tag["slug"]}, tag)
    report("tag", tag["name"], c)

def prefix(body, label):
    obj, c = ensure("ipam/prefixes/", {"prefix": body["prefix"]}, body)
    if c:
        report("prefix", label, True)
    elif not reconcile_prefix(obj, body):
        report("prefix", label, False)


for env in plan["environments"]:
    tags = [{"slug": env["tag"]}]
    prefix({"prefix": env["supernet"], "status": "container",
            "role": roles["environment-supernet"], "tags": tags,
            "description": f"{env['name']} environment supernet"},
           f"{env['supernet']:<16} {env['name']} supernet (container)")
    for p in env["prefixes"]:
        prefix({"prefix": p["prefix"], "status": p.get("status", "active"),
                "role": roles[p["role"]], "tags": tags, "description": p["description"]},
               f"{p['prefix']:<16} {p['description']}")

print(f"\n{counts['created']} created, {counts['updated']} updated.")
