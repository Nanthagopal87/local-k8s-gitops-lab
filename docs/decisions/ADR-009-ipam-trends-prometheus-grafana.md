# ADR-009: IPAM trend charts with a NetBox exporter, Prometheus and Grafana

## Status

Accepted on 2026-11-26.

## Context

NetBox (ADR-008) holds the CloudOpsEdge (coe) IP plan and shows its **current** state: the
home dashboard has utilisation bars, but NetBox keeps no history, so it cannot answer "how
fast is this region block filling up?". Three options were compared on 2026-11-26: a nightly
CSV snapshot in Git (lightest), Prometheus + Grafana (chosen), and the same with
VictoriaMetrics instead of Prometheus. This lab's `CLAUDE.md` asks for the reason, the
resource impact and a lighter alternative before adding a component; that comparison is the
record of it. The lab's own notes already list "add observability (Prometheus + Grafana)" as
the next extension, so one stack serves both goals.

Facts checked on 2026-11-26:

* `prometheus-community/prometheus` **29.47.2** (Prometheus v3.16.0, 2026-11-11). 30.x is a
  new major released 2026-11-25, too new to pin.
* The Grafana chart **moved**: `grafana/helm-charts` marks `grafana` as deprecated (last
  10.5.15); the maintained chart is `grafana-community/grafana`, pinned at **14.4.1**
  (Grafana 13.3.1, 2026-11-10).
* The Grafana chart generates its admin password at render time with `lookup` +
  `randAlphaNum`: the same perpetual-drift trap as NetBox under Argo CD (ADR-008).
* The Prometheus chart's default scrape configuration discovers the whole cluster (nodes,
  pods, cAdvisor) and pulls in Alertmanager, node-exporter, kube-state-metrics and
  pushgateway subcharts.
* About 3.5 GiB of memory was available on the 7.5 GiB WSL VM before this change.

## Decision

1. **A NetBox exporter** (`argocd/apps/netbox-exporter/`): a standard-library Python script
   on the pinned official `python:3.13.18-alpine3.24` image, shipped as a ConfigMap (no
   custom image build: this lab has no Docker). It reads the NetBox API at most once a
   minute (cached) and publishes:
   * `coe_prefix_utilization_ratio`: NetBox's own rule (containers count child prefixes);
   * `coe_prefix_assigned_ratio`: share covered by **real subnets only**. Added because by
     NetBox's rule a spoke VPC whose two `/17` region blocks exist reads 100%, which is true
     but useless for capacity;
   * aggregate ratios, prefix counts by tenant/environment/role/status, IP address and range
     counts, and exporter health.
   Labels carry tenant, environment, site, role, VPC and project from NetBox.
2. **Prometheus**, narrow scope: scrapes only itself and the exporter (replacing the default
   cluster-wide discovery), all four subcharts off, `rbac.create: false` (no Kubernetes
   discovery means no cluster permissions), 30-day retention capped at 1500 MB on a 2 GiB
   PVC. Four alert rules (`CoeRegionBlockFillingUp` > 80%, `CoeVpcFillingUp` > 80%,
   `CoeNetboxExporterDown`, `CoeNetboxCollectionFailing`), validated with `promtool`. No
   Alertmanager: alerts show as firing in Prometheus and on the Grafana dashboard.
3. **Grafana**, everything provisioned from Git: the Prometheus data source in values, the
   dashboard as JSON in `argocd/apps/grafana/dashboards/` (a Kustomize ConfigMap, the
   Application's third source). No persistence, no sidecar, `rbac.create: false`, telemetry
   and update checks off, sign-up and anonymous access off.
4. **Secrets outside Git and Argo CD**, as in ADR-008:
   `argocd/apps/netbox-exporter/manual/create-secrets.sh` creates `grafana-admin` (random)
   and `netbox-exporter-token` (a copy of the NetBox API token; Secrets cannot be read
   across namespaces).
5. Three Applications (`netbox-exporter`, `prometheus`, `grafana`), all **manual sync**, in
   namespace `monitoring`. Access by port-forward only: Grafana on `localhost:3000`,
   Prometheus on `localhost:9090`.
6. Limits: Prometheus 512Mi, Grafana 384Mi, exporter 128Mi (requests 128/128/32Mi).

## Alternatives Considered

| Option | Verdict |
|---|---|
| Nightly CSV snapshot in Git + static chart page | Almost free, but no alerting and no ad-hoc queries. Rejected in favour of a stack the lab needs anyway. |
| VictoriaMetrics single-node instead of Prometheus | Lighter (~350–500 MB total), same query language; less standard for learning. Kept as the fallback if memory gets tight. |
| kube-prometheus-stack (Prometheus Operator) | Full cluster monitoring, but far heavier and more moving parts than one exporter needs. Rejected. |
| Exporter with `prometheus_client` | Needs a package install (custom image or install at start). The text format is simple enough for the standard library. Rejected. |
| Grafana sidecar to discover dashboards | Needs Kubernetes API permissions; a mounted ConfigMap does not. Rejected. |

## Consequences

* Trend charts show the **plan** filling up (prefixes created by `seed.py`/`allocate.py`),
  not real GCP usage; real usage needs IP addresses in NetBox (a GCP sync) or GCP's own
  Network Analyzer.
* History starts at first sync: the 7-day growth panel needs days of data to mean much.
* The exporter's API token is a copy: if `netbox-api-token` is rotated, delete
  `monitoring/netbox-exporter-token` and re-run the script.
* Grafana edits made in the UI are lost on restart by design; change the JSON in Git.
* About 600–800 MB more memory while running.
