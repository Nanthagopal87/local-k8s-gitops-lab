# netbox-demo: CloudOpsEdge (coe) IPAM in NetBox

Data for the NetBox instance deployed by the `netbox` Argo CD Application
([ADR-008](../docs/decisions/ADR-008-netbox-helm-multi-source.md)). Like `atlantis-demo/`,
this folder is **not** referenced by any Argo CD Application: Argo CD deploys NetBox itself,
while the *contents* of NetBox are data, written through NetBox's REST API.

[`coe.yaml`](coe.yaml) is the source of truth for **CloudOpsEdge (coe)**, built in stages:

| Stage | Contents | Status |
|---|---|---|
| 1. Organisation and address space | Tenant group `coe`; RIRs RFC 1918 and RFC 6598; aggregates `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`; regions Google Cloud → India; sites `asia-south1` (Mumbai, primary) and `asia-south2` (Delhi, secondary/DR) | Done 2026-11-26 |
| 2. Business units | Tenants under the `coe` group: `bootstrap`, `platform`, `bu1`, `bu2` | Done 2026-11-26 |
| 3. Environments, VPCs, subnets | Hubs (prd: landingzone + hybridconn, dc + dr; npd: dc), spokes per tenant and environment, standard subnets per region block, GKE Pod ranges in RFC 6598, BGP link-local block, Cloud Router ASNs. 155 prefixes | Done 2026-11-26 |

## Layout (stage 3)

Environments: `bootstrap` prd; `platform` npd and prd; `bu1`/`bu2` dev, stg and prd.
**prd = dc (`asia-south1`, Mumbai) + dr (`asia-south2`, Delhi)**; dev, stg and npd are dc only.

| Block | Tenant | Contents |
|---|---|---|
| `10.0.0.0/16` | platform | Hub prd: landingzone `/18` dc + dr, hybridconn `/18` dc + dr |
| `10.1.0.0/16` | bootstrap | prd, CI/CD workers `/24` in dc and dr |
| `10.2.0.0/16` | platform | npd Shared VPC (dc) |
| `10.3.0.0/16` | platform | prd Shared VPC (dc + dr) |
| `10.4.0.0/16` | platform | Hub npd: landingzone + hybridconn (dc) |
| `10.16.0.0/12` | bu1 | dev `10.16/16`, stg `10.17/16`, prd `10.18/16` |
| `10.32.0.0/12` | bu2 | dev `10.32/16`, stg `10.33/16`, prd `10.34/16` |
| `100.64.0.0/14`, `100.68.0.0/14`, `100.72.0.0/14` | bu1, bu2, platform | GKE Pods, one `/16` per cluster (dc and dr separate) |
| `169.254.0.0/16` | platform | BGP peering `/29`s (Interconnect, HA VPN) |

Each spoke `/16`: first `/17` = dc, second `/17` = dr. Every region block has the same subnets
(third-octet offset): app `/22` (.0), GKE nodes `/22` (.4), GKE Services `/22` (.8), proxy-only
`/24` (.12), PSC NAT `/24` (.13), serverless connectors `/24` (.14), Private Service Access
`/20` (.16), db `/22` (.32), dwh `/22` (.36); the rest is free for `allocate.py`.
Cloud Router ASNs (RFC 6996): 64512 hub prd dc, 64513 hub prd dr, 64514 hub npd dc.

### GCP names (custom fields on prefixes)

`gcp_project` (project that owns the VPC, i.e. the Shared VPC host) and `gcp_vpc` (VPC
network name), both **planned names**, validated against GCP's naming rules (a bad value is
rejected with HTTP 400). Filter with `?cf_gcp_vpc=vpc-bu1-prd` in the API or UI.

| Scope | `gcp_project` | `gcp_vpc` |
|---|---|---|
| Hub prd / npd | `coe-net-hub-prd` / `coe-net-hub-npd` | `vpc-landingzone-<env>`, `vpc-hybridconn-<env>` |
| bootstrap prd | `coe-bootstrap-prd-cicd` | `vpc-bootstrap-prd` |
| Spokes | `coe-<tenant>-<env>-net-host` | `vpc-<tenant>-<env>` |
| GKE Pod ranges | the spoke host project | the spoke VPC |

Tenant blocks and the BGP container span several VPCs and carry no values; each hub `/16`
carries only the project (it holds two VPCs).

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
  tenant's region block, instead of a person picking a CIDR; the new prefix inherits tenant,
  site and environment tag:
  `python3 netbox-demo/allocate.py --tenant bu1 --env prd --site asia-south1 --length 24 --role app-subnet --dry-run`

* **`reconcile.py`**: compares Google Cloud subnets with the plan, **read-only on both
  sides**. Input is `gcloud compute networks subnets list --format=json`, either live
  (`--project <id>`, repeatable) or a saved export (`--file`). Every primary and secondary
  range (GKE Pods/Services) is checked for CIDR, `gcp_project`, `gcp_vpc`, region ↔ site and
  purpose ↔ role. Findings: `OK`, `MISMATCH`, `CONFLICT` (overlaps a planned range of a
  different size), `UNPLANNED` (in GCP, inside the plan's free space), `OUT_OF_PLAN`, and
  `NOT_BUILT` (planned for a scanned project, absent in GCP). Exit code 1 on drift (all but
  `OK`/`NOT_BUILT`), so it can gate CI; `--json` for machine output. PSA ranges and
  serverless connectors are not subnets and are not checked yet.

  ```bash
  python3 netbox-demo/reconcile.py --file netbox-demo/samples/gcp-subnets-bu1-prd.json
  ```

  [`samples/gcp-subnets-bu1-prd.json`](samples/gcp-subnets-bu1-prd.json) is **fictional**
  test data with one case per finding (each subnet's `description` says what it should
  produce). Run on 2026-11-26: OK 6, MISMATCH 3, CONFLICT 1, UNPLANNED 1, OUT_OF_PLAN 1,
  NOT_BUILT 6, exit code 1; the OK-only subset exits 0.

## History

* 2026-11-25: a fictional "Example Org" plan was seeded to learn the API, then deleted on
  2026-11-26 (all 33 objects) to start coe from scratch. See Git history for those files.
