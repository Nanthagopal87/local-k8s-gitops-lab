#!/usr/bin/env python3
"""Make NetBox match a YAML source of truth (default: coe.yaml). Idempotent.

Usage (with the NetBox port-forward running):
    python3 netbox-demo/seed.py            # apply coe.yaml
    python3 netbox-demo/seed.py --dry-run  # show what would change, change nothing

For every object in the YAML: create it if missing; if it exists, PATCH only the fields
that differ (so the YAML is the desired state). Nothing is ever deleted, and objects that
exist only in NetBox (for example prefixes handed out by allocate.py) are left alone.

References between objects are written as slugs (or CIDRs for prefixes) in the YAML and
resolved to NetBox IDs here, e.g. `rir: rfc-1918`, `region: gcp-india`, `tenant: bu-x`.
"""
import argparse
import pathlib

import yaml

from nbapi import call, get_one

# section -> (API endpoint, field used to find an existing object, reference fields)
# Reference fields map a YAML key to (endpoint, lookup field) for slug -> id resolution.
KINDS = {
    "tenant_groups": ("tenancy/tenant-groups/", "slug", {"parent": ("tenancy/tenant-groups/", "slug")}),
    "tenants":       ("tenancy/tenants/", "slug", {"group": ("tenancy/tenant-groups/", "slug")}),
    "rirs":          ("ipam/rirs/", "slug", {}),
    "aggregates":    ("ipam/aggregates/", "prefix", {"rir": ("ipam/rirs/", "slug"),
                                                     "tenant": ("tenancy/tenants/", "slug")}),
    "regions":       ("dcim/regions/", "slug", {"parent": ("dcim/regions/", "slug")}),
    "sites":         ("dcim/sites/", "slug", {"region": ("dcim/regions/", "slug"),
                                              "tenant": ("tenancy/tenants/", "slug")}),
    "roles":         ("ipam/roles/", "slug", {}),
    "tags":          ("extras/tags/", "slug", {}),
    "custom_fields": ("extras/custom-fields/", "name", {}),
    "asns":          ("ipam/asns/", "asn", {"rir": ("ipam/rirs/", "slug"),
                                            "tenant": ("tenancy/tenants/", "slug"),
                                            "sites": ("dcim/sites/", "slug")}),
    "prefixes":      ("ipam/prefixes/", "prefix", {"role": ("ipam/roles/", "slug"),
                                                   "tenant": ("tenancy/tenants/", "slug"),
                                                   "site": ("dcim/sites/", "slug")}),
}

ap = argparse.ArgumentParser()
ap.add_argument("file", nargs="?", default=str(pathlib.Path(__file__).parent / "coe.yaml"))
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

plan = yaml.safe_load(pathlib.Path(args.file).read_text())
unknown = set(plan) - set(KINDS)
if unknown:
    raise SystemExit(f"unknown section(s) in {args.file}: {', '.join(sorted(unknown))}")

counts = {"created": 0, "updated": 0, "unchanged": 0}
_ref_cache = {}


def ref_id(endpoint, field, value):
    key = (endpoint, field, value)
    if key not in _ref_cache:
        obj = get_one(endpoint, **{field: value})
        if obj is None:
            if args.dry_run:   # created earlier in this same (simulated) run
                return f"<new {value}>"
            raise SystemExit(f"reference not found: {endpoint}?{field}={value}")
        _ref_cache[key] = obj["id"]
    return _ref_cache[key]


def normalise(value):
    """Turn NetBox's nested API representation into what we send."""
    if isinstance(value, dict):
        if "value" in value and "label" in value:   # choice field, e.g. status
            return value["value"]
        if "id" in value:                           # nested object reference
            return value["id"]
    if isinstance(value, list) and value and isinstance(value[0], dict):
        if "slug" in value[0] and "color" in value[0]:
            return sorted(t["slug"] for t in value)  # tags
        if "id" in value[0]:
            return sorted(o["id"] for o in value)    # many-to-many refs, e.g. ASN sites
    return value


for section, items in plan.items():
    endpoint, match_field, refs = KINDS[section]
    for item in items or []:
        body = dict(item)
        for k, (ref_ep, ref_field) in refs.items():
            if isinstance(body.get(k), list):
                body[k] = sorted(ref_id(ref_ep, ref_field, v) for v in body[k])
            elif body.get(k) is not None:
                body[k] = ref_id(ref_ep, ref_field, body[k])
        if "tags" in body:
            body["tags"] = [{"slug": t} for t in body["tags"]]
        if section == "prefixes":
            body.setdefault("status", "active")
            site = body.pop("site", None)
            if site is not None:   # NetBox 4.2+: a prefix is scoped to a site via scope_*
                body["scope_type"], body["scope_id"] = "dcim.site", site
        label = f"{({'prefixes': 'prefix'}.get(section, section[:-1])):<12} {item[match_field]}"

        current = get_one(endpoint, **{match_field: item[match_field]})
        if current is None:
            counts["created"] += 1
            print(f"create  {label}")
            if not args.dry_run:
                status, data = call("POST", endpoint, body)
                if status != 201:
                    raise SystemExit(f"POST {endpoint} -> {status}: {data}")
            continue

        diff = {}
        for k, v in body.items():
            if k == "custom_fields":   # compare only the custom fields this YAML sets
                have = current.get("custom_fields") or {}
                if any(have.get(cf) != val for cf, val in v.items()):
                    diff[k] = v
                continue
            want = sorted(t["slug"] for t in v) if k == "tags" else v
            if normalise(current.get(k)) != want:
                diff[k] = v
        if not diff:
            counts["unchanged"] += 1
            print(f"ok      {label}")
            continue
        counts["updated"] += 1
        print(f"update  {label}  ({', '.join(sorted(diff))})")
        if not args.dry_run:
            status, data = call("PATCH", f"{endpoint}{current['id']}/", diff)
            if status != 200:
                raise SystemExit(f"PATCH {endpoint}{current['id']}/ -> {status}: {data}")

mode = " (dry run: nothing changed)" if args.dry_run else ""
print(f"\n{counts['created']} created, {counts['updated']} updated, "
      f"{counts['unchanged']} unchanged{mode}.")
