# ADR-008: NetBox via an upstream Helm chart, as a multi-source Argo CD Application

## Status

Accepted on 2026-11-25. Implementation status is tracked in the README's Current Status table.

## Context

The goal is to learn NetBox as an **IP address management (IPAM)** source of truth: supernets and per-environment prefixes, allocating the next free block through the REST API (`/api/ipam/prefixes/<id>/available-prefixes/`), and later reconciling it against what actually exists in a cloud account. This lab had not yet used **Helm** (listed under "Future topics" in `CLAUDE.md` and as not covered in earlier notes), and NetBox publishes an official chart, so it is a good first Helm workload.

The facts behind the decisions, checked on 2026-11-25:

* **Chart:** `netbox` from `https://charts.netbox.oss.netboxlabs.com/`. It publishes several releases a day (8.3.112–8.3.115 in the 48 hours before this ADR).
* **Bundled backing services:** the chart depends on the Bitnami `postgresql` and `valkey` subcharts, whose images are tagged **`latest`** (`bitnami/postgresql:latest`, `bitnami/valkey:latest`) since Bitnami restricted its free image catalogue. Everything else in this lab is pinned (for example Atlantis `v0.48.0`).
* **Render-time secrets:** with `secretKey`, `apiTokenPeppers` and the superuser password left blank, the chart generates random values, relying on Helm's `lookup` to keep them stable across upgrades. **Argo CD renders charts with `helm template`, where `lookup` returns nothing**, so the values would change on every render: a permanently OutOfSync Application, and API tokens that break whenever the pepper changes. Two local renders with the values in this ADR were byte-identical, which confirms the fix below.
* **Capacity:** the WSL2 VM has 7.5 GiB (about 4.8 GiB available before this change); 14 vCPU; 926 GB free disk.

## Decision

1. **One Argo CD Application (`netbox`) with three sources** (`argocd/applications/netbox.yaml`):
   * the upstream chart, **pinned to `8.3.101`** (released 2026-11-10, NetBox `v4.7.4`), at least two weeks old when chosen; never a version range;
   * this repository as a **`$values` reference**, so `argocd/apps/netbox/values.yaml` is reviewed and versioned in Git rather than typed inline into the Application;
   * this repository again for **`argocd/apps/netbox/data/`**, plain Kustomize manifests for the backing services.
2. **Backing services with pinned official images instead of the Bitnami subcharts** (`postgresql.enabled: false`, `valkey.enabled: false`):
   * `docker.io/library/postgres:17.11-alpine3.24` (pushed 2026-11-11), one replica, a 1Gi `local-path` PVC, `Recreate` strategy, non-root as UID/GID 70;
   * `docker.io/valkey/valkey:8.1.11-alpine3.24` (pushed 2026-10-27), no persistence (task queue and cache are disposable), 64 MB `maxmemory`, read-only root filesystem, non-root as UID 999 / GID 1000.
   Both UIDs were confirmed against the images themselves (`kubectl run … -- id`), not assumed, after the `runAsNonRoot` lesson in ADR-007. Sync wave `-1` (namespace `-2`) puts them ahead of NetBox, whose chart also waits for its backend in an init container.
3. **No Secret is rendered by Helm or managed by Argo CD.** `argocd/apps/netbox/manual/create-secrets.sh` creates `netbox-config` (`secret_key`, `api_token_peppers`, `email_password`), `netbox-superuser` (`username`, `email`, `password`, `api_token`) and `netbox-db` (`postgresql-password`, shared by PostgreSQL and NetBox) with random values generated locally. Unlike ADR-007's committed fake token, **no value is committed at all**. The script is idempotent and never regenerates an existing Secret: PostgreSQL reads its password only when it first initialises the volume.
4. **Manual sync** (no `syncPolicy`), like every Application here except `phase7-sandbox`.
5. **Footprint limits:** NetBox requests 100m / 512Mi (limit 1536Mi, with `GRANIAN_WORKERS=2` instead of the image default 4: the first start with 4 workers was OOMKilled at 1Gi), worker 50m / 192Mi (limit 512Mi), PostgreSQL 50m / 128Mi (limit 384Mi), Valkey 20m / 32Mi (limit 128Mi). NetBox media persistence is off (not needed for IPAM practice). Housekeeping stays at the chart default (off: NetBox ≥ 4.6 does it internally).
6. **Access by `kubectl port-forward` only**, the same pattern as the Argo CD UI; no Ingress, nothing exposed through nginx or Traefik.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Chart with bundled Bitnami PostgreSQL/Valkey | About ten minutes quicker, but `latest` images break the lab's pinning rule and can change underneath a running database. Rejected. |
| Bitnami subcharts with image overrides to pinned official images | The Bitnami charts expect Bitnami images (paths, users, entrypoint scripts); fighting that is more fragile than two small manifests. Rejected. |
| Values inline in the Application (`helm.valuesObject`) | Simpler single source, but hides the values inside an Argo CD object and skips the `$values` multi-source pattern worth learning. Rejected. |
| Docker Compose (NetBox's `netbox-docker`) | Lightest to start, but Docker Desktop is prohibited in this lab and it would not exercise Helm through Argo CD. Rejected. |
| Let the chart generate the secrets | Causes perpetual drift under Argo CD (see Context). Rejected. |

## Consequences

* First Helm-rendered and first multi-source Application in the lab; Argo CD fetches the chart from the chart repository and the values and manifests from this GitHub repository, so **changes take effect only after they are pushed to `main`**.
* About 1–1.3 GiB of extra memory while running. The Application can be left OutOfSync or the Deployments scaled to zero when not in use.
* Valkey has no authentication. Acceptable only because it is a ClusterIP Service on a single-node laptop cluster; a shared or exposed setup would need auth and TLS.
* Chart upgrades are deliberate: change `targetRevision` (and `image.tag` in values) in one reviewed commit, read the chart's release notes, sync manually.
* The data used in NetBox must be fictional (documentation ranges and invented environment names): this lab is public and separate from any employer's systems.
