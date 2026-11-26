#!/usr/bin/env python3
"""Allocate the next free prefix inside a coe region block, the way a self-service workflow
(for example a Backstage template) would: ask NetBox, don't pick a CIDR by hand.

Usage (with the NetBox port-forward running):
    python3 netbox-demo/allocate.py --tenant bu1 --env prd --site asia-south1 --length 24 --role app-subnet --dry-run
    python3 netbox-demo/allocate.py --tenant bu1 --env prd --site asia-south1 --length 24 --role app-subnet \
        --description "bu1 prd dc - payments app subnet"

The region block is the container prefix with role "region-block" for that tenant,
environment tag (env-<env>) and site. The new prefix inherits tenant, site and tag, and gets
the role you pass. Uses POST /api/ipam/prefixes/<id>/available-prefixes/, which picks and
creates the block in one call, so two concurrent requests can't receive the same range.
"""
import argparse
import ipaddress

from nbapi import call, get_one

ap = argparse.ArgumentParser()
ap.add_argument("--tenant", required=True, help="bootstrap, platform, bu1, bu2")
ap.add_argument("--env", required=True, help="dev, stg, npd, prd")
ap.add_argument("--site", required=True, choices=["asia-south1", "asia-south2"])
ap.add_argument("--length", type=int, required=True, help="prefix length, e.g. 24")
ap.add_argument("--role", required=True, help="role slug, e.g. app-subnet, db-subnet")
ap.add_argument("--description", default="allocated by allocate.py")
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

site = get_one("dcim/sites/", slug=args.site)
role = get_one("ipam/roles/", slug=args.role)
if role is None:
    raise SystemExit(f"unknown role '{args.role}'")
block = get_one("ipam/prefixes/", tenant=args.tenant, tag=f"env-{args.env}",
                role="region-block", scope_type="dcim.site", scope_id=site["id"])
if block is None:
    raise SystemExit(f"no region block for {args.tenant} {args.env} {args.site} "
                     "(dev, stg and npd exist only in asia-south1)")

_, free = call("GET", f"ipam/prefixes/{block['id']}/available-prefixes/")
fits = [f["prefix"] for f in free if ipaddress.ip_network(f["prefix"]).prefixlen <= args.length]
print(f"{args.tenant} {args.env} {args.site} region block {block['prefix']}: "
      f"free blocks that fit a /{args.length}: {', '.join(fits) or 'none'}")
if not fits:
    raise SystemExit("no space left for that size")

if args.dry_run:
    print(f"dry run: would allocate {next(ipaddress.ip_network(fits[0]).subnets(new_prefix=args.length))}")
    raise SystemExit(0)

status, data = call("POST", f"ipam/prefixes/{block['id']}/available-prefixes/", {
    "prefix_length": args.length, "status": "active", "description": args.description,
    "role": role["id"], "tenant": block["tenant"]["id"],
    "scope_type": "dcim.site", "scope_id": site["id"],
    "tags": [{"slug": t["slug"]} for t in block["tags"]]})
if status != 201:
    raise SystemExit(f"allocation failed ({status}): {data}")
print(f"allocated {data['prefix']} (id {data['id']}): {data['description']}")
