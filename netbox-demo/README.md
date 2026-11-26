# netbox-demo: CloudOpsEdge (coe) IPAM in NetBox

Data for the NetBox instance deployed by the `netbox` Argo CD Application
([ADR-008](../docs/decisions/ADR-008-netbox-helm-multi-source.md)). Like `atlantis-demo/`,
this folder is **not** referenced by any Argo CD Application: Argo CD deploys NetBox itself,
while the *contents* of NetBox are data, written through NetBox's REST API.

[`coe.yaml`](coe.yaml) is the source of truth for **CloudOpsEdge (coe)**, built in stages:

| Stage | Contents | Status |
|---|---|---|
| 1. Organisation and address space | Tenant group `coe`; RIRs RFC 1918 and RFC 6598; aggregates `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`; regions Google Cloud → India; sites `asia-south1` (Mumbai, primary) and `asia-south2` (Delhi, secondary/DR) | Done 2026-11-26 |
| 2. Business units | One tenant per BU under the `coe` group | Next |
| 3. Environments, VPCs, subnets | Container prefixes per environment, subnets and GKE ranges with roles, scoped to a site | Later |

Address-space rule: **RFC 1918** for VPC subnets, GKE nodes and Services; **RFC 6598**
(`100.64.0.0/10`) only for GKE Pod secondary ranges.

## Scripts

Both read the API token from the `netbox-api-token` Secret through `kubectl` (never printed
or stored) and talk to `http://127.0.0.1:8001`, so start the port-forward first:

```bash
kubectl -n netbox port-forward svc/netbox 8001:80 --address 127.0.0.1 &
```

* **`seed.py`**: makes NetBox match `coe.yaml`. Creates what is missing and PATCHes only the
  fields that differ; never deletes, never touches objects that exist only in NetBox.
  `--dry-run` shows the plan first. A second run reports everything `unchanged`.
* **`allocate.py`**: asks NetBox's `available-prefixes` API for the next free block inside a
  container prefix, instead of a person picking a CIDR. It will be updated for coe's
  environments in stage 3.

## History

* 2026-11-25: a fictional "Example Org" plan was seeded to learn the API, then deleted on
  2026-11-26 (all 33 objects) to start coe from scratch. See Git history for those files.
