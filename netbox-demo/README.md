# netbox-demo: a fictional IP plan in NetBox

Data for the NetBox instance deployed by the `netbox` Argo CD Application
([ADR-008](../docs/decisions/ADR-008-netbox-helm-multi-source.md)). Like `atlantis-demo/`,
this folder is **not** referenced by any Argo CD Application: Argo CD deploys NetBox itself,
while the *contents* of NetBox are data, written through NetBox's REST API.

Everything here is invented: "Example Org", `10.192.0.0/12` (an arbitrary RFC 1918 block),
and the environment names. It describes no real network.

## The plan ([ip-plan.yaml](ip-plan.yaml))

| Block | Use |
|---|---|
| `10.192.0.0/12` | Aggregate: the organisation's private space |
| `10.192.0.0/16` | `shared`: hub/shared services, hybrid connectivity (reserved), platform tools |
| `10.193.0.0/16` | `dev`: workload subnet, GKE nodes, GKE Pods `/18`, GKE Services `/24` |
| `10.194.0.0/16` | `test`: same shape as dev |
| `10.195.0.0/16` | `prod`: primary and DR workload subnets, GKE nodes, Pods `/18`, Services `/24` |
| `10.196.0.0/14` and up | Unallocated, for future environments |

Each `/16` is a NetBox **container** prefix; children carry a role (workload subnet, GKE
nodes, GKE Pods, …) and an environment tag. Free space is left inside every environment.

## Scripts

Both read the API token from the `netbox-api-token` Secret through `kubectl` (it is never
printed or stored) and talk to `http://127.0.0.1:8001`, so start the port-forward first:

```bash
kubectl -n netbox port-forward svc/netbox 8001:80 --address 127.0.0.1 &
```

* **`seed_ip_plan.py`**: makes NetBox match `ip-plan.yaml`. Creates what is missing and
  corrects drifted prefix fields (description, status, role, tags); never deletes, and never
  touches prefixes that exist only in NetBox. Re-running prints `0 created, 0 updated`.
* **`allocate.py`**: asks NetBox for the next free block of a given size inside an
  environment, the way a self-service workflow (for example a Backstage template) would,
  instead of a person picking a CIDR from a spreadsheet:

  ```bash
  python3 netbox-demo/allocate.py --env prod --length 22 --dry-run
  python3 netbox-demo/allocate.py --env prod --length 22 --description "Prod - payments subnet"
  ```

  It uses `POST /api/ipam/prefixes/<id>/available-prefixes/`, which picks and creates the
  block in one call, so two concurrent requests cannot receive the same range.

## First run (2026-11-25)

* Seed: 31 objects created (RIR, aggregate, 6 roles, 3 tags, 4 supernets, 16 child
  prefixes); a second run created and updated nothing.
* Allocation: two `/22` requests in prod received `10.195.16.0/22` and then
  `10.195.20.0/22`, the first free blocks after the seeded ranges.

## Next idea (not built)

Reconcile NetBox against what really exists: list subnets and secondary ranges from a
cloud account (or a mock JSON export) and report ranges that exist in the cloud but not in
NetBox, and the reverse.
