#!/usr/bin/env python3
"""Allocate the next free prefix inside an environment supernet, the way a self-service
workflow (for example a Backstage template) would: ask NetBox, don't pick a CIDR by hand.

Usage (with the NetBox port-forward running):
    python3 netbox-demo/allocate.py --env prod --length 22 --description "Prod - payments subnet"
    python3 netbox-demo/allocate.py --env prod --length 22 --dry-run   # only show the candidate

Uses POST /api/ipam/prefixes/<id>/available-prefixes/, which picks the first free block of
the requested size and creates it in one call, so two concurrent requests can't receive the
same range.
"""
import argparse
import ipaddress

from nbapi import call, get_one

ap = argparse.ArgumentParser()
ap.add_argument("--env", required=True, choices=["shared", "dev", "test", "prod"])
ap.add_argument("--length", type=int, required=True, help="prefix length, e.g. 22")
ap.add_argument("--description", default="allocated by allocate.py")
ap.add_argument("--role", default="workload-subnet")
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

supernet = get_one("ipam/prefixes/", description=f"{args.env} environment supernet")
if not supernet:
    raise SystemExit(f"no supernet for '{args.env}': run seed_ip_plan.py first")
pid = supernet["id"]

status, free = call("GET", f"ipam/prefixes/{pid}/available-prefixes/")
fits = [f["prefix"] for f in free
        if ipaddress.ip_network(f["prefix"]).prefixlen <= args.length]
print(f"{args.env} supernet {supernet['prefix']}: {len(free)} free block(s); "
      f"blocks that fit a /{args.length}: {', '.join(fits) or 'none'}")

if args.dry_run:
    if fits:
        first = next(ipaddress.ip_network(fits[0]).subnets(new_prefix=args.length))
        print(f"dry run: would allocate {first}")
    raise SystemExit(0)

role = get_one("ipam/roles/", slug=args.role)
status, data = call("POST", f"ipam/prefixes/{pid}/available-prefixes/", {
    "prefix_length": args.length, "status": "active", "description": args.description,
    "role": role["id"] if role else None,
    "tags": [{"slug": t["slug"]} for t in supernet["tags"]]})
if status != 201:
    raise SystemExit(f"allocation failed ({status}): {data}")
print(f"allocated {data['prefix']} (id {data['id']}): {data['description']}")
