# ADR-003: GitOps ownership boundaries: Argo CD owns intent, not generated resources

## Status

Accepted and implemented on 2026-09-20 (Phase 4).

## Context

Phase 4 brings existing, manually applied resources under Argo CD without recreating them. Two areas needed a deliberate boundary:

* **`k8s-learning` (Phase 2).** The namespace mixes long-lived resources (Deployment, Service, Ingress, ConfigMap, PVC, RBAC) with things that should never be Git-driven: a fake demo Secret and disposable demos.
* **Traefik.** It is installed by k3s, not by us. Inspection showed a chain of owners: the manifest on the node (root-owned) creates a k3s `Addon`, which creates the `HelmChart`; the Helm controller installs the release, whose `Deployment` and `Service` carry `managed-by: Helm`; because the Service is `type: LoadBalancer`, the k3s service controller creates the `svclb-traefik` DaemonSet (owner marker: `Service/traefik`). None of these use `ownerReferences`; ownership is recorded in `objectset.rio.cattle.io/*` annotations and Helm labels. Our own contribution to this chain is a single `HelmChartConfig` (ADR-001), which only `kubectl` had written.

If Argo CD tried to own objects that another controller also reconciles, the two would fight over the same fields.

## Decision

1. **Argo CD manages configuration we authored, at the smallest boundary that expresses our intent.** For Traefik that boundary is the single `HelmChartConfig`. It does **not** manage the `HelmChart`, the Traefik `Deployment`/`Service`, the ServiceLB `DaemonSet` or its Pods, and we do not put copies of them in Git.
2. **A directory is the ownership boundary for an Application.** Everything in `argocd/apps/k8s-learning/` is Argo CD's. The Traefik Application points at `kubernetes/platform/traefik` with `directory.include: helmchartconfig.yaml`, so exactly one file is ever read.
3. **Adopt in place, never recreate.** Manifests were `git mv`d (single source of truth, no copies) and were already identical to the live objects. Before any sync, the Git manifests were compared with the live cluster (`kubectl diff`, server-side dry-run, and Argo CD's own field comparison). The only differences were metadata annotations (Argo CD's tracking marker and our guardrail annotation), so a sync only stamps ownership.
4. **The fake demo Secret stays outside Argo CD** (`kubernetes/learning/secret/`), together with the disposable Pod, the NodePort Service and the `pvc-writer` Pod. Kubernetes Secret data is base64-encoded, not encrypted, the lab has no secrets-management system, and real credentials never go into Git. A real approach (for example sealed or external secrets) is a separate future decision.
5. **Data-bearing objects carry a guardrail:** `argocd.argoproj.io/sync-options: Prune=false,Delete=false` on the Namespace and the PVC, so Argo CD can never delete them even if pruning is enabled later.
6. **Sync stays manual for every Application** (no `automated`, no `selfHeal`, no `prune`). Enabling any of them needs explicit approval and is evaluated in `docs/argocd/gitops-adoption.md`.
7. **Public-repository hygiene without rewriting history:** machine-specific values in current docs were replaced by placeholders in normal commits.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Manage the k3s `HelmChart` with Argo CD | The manifest that creates it lives on the node and is re-applied by k3s's addon controller. Two controllers would own it. Rejected. |
| Manage the Traefik `Deployment`/`Service` (for example by exporting them) | Helm owns them; every Helm upgrade would conflict with Argo CD, and the values live in the `HelmChartConfig` anyway. Rejected. |
| Manage the ServiceLB `DaemonSet` | It is generated from the Service and recreated by the service controller. Rejected. |
| Point one Application at `kubernetes/learning` with `directory.recurse` | Would pull in the fake Secret and the disposable demos, or need a growing exclude list. A dedicated directory is clearer and safer. Rejected. |
| Copy manifests into `argocd/apps/` and leave the originals | Two sources of truth that will drift. Rejected in favour of `git mv`. |
| Delete and recreate the resources under Argo CD | Unnecessary and dangerous (PVC data). Adoption in place was possible, so this was never needed. |
| Put the Secret under Argo CD "because it is fake" | Sets a habit and a path that real credentials could follow. Rejected. |
| Rewrite history to remove old identifiers from the public repo | Forbidden for this phase, and the author identity is in commit metadata anyway. Old commits still contain the earlier docs. |

## Consequences

Positive:

* Adoption caused **no recreation**: every UID, ReplicaSet, Pod, PVC and PV was identical before and after, and the Traefik sync produced no interruption (measured, see the adoption doc).
* The boundary is visible in the repository layout and in each Application (`argocd/applications/*.yaml` comments), and enforced by Argo CD itself (Traefik Application tracks exactly one resource).
* Clear, documented manual exceptions (`kubernetes/learning/README.md`).

Negative / to remember:

* **Do not `kubectl apply` an Argo CD-managed manifest by hand.** Argo CD adds a tracking annotation that is not in the file, and a hand apply removes it. Use Argo CD (or its diff) instead.
* **A metadata-only annotation change still bumps a Deployment's `generation`** (verified on a throwaway object). It is not a rollout.
* The **fake Secret is not GitOps-managed**: recreating the namespace by hand would need it applied manually. That is accepted for now.
* **Traefik configuration changes are high-impact.** A bad edit to the `HelmChartConfig` could move Traefik back onto 80/443 and shadow nginx. Manual sync is therefore kept for `traefik-config`, and this is the strongest reason to be careful about ever enabling automation there.
* Old commits in the public history still contain the earlier machine-specific values, and commit metadata contains the author email.

Rollback: delete the Application objects (`kubectl delete -f argocd/applications/<name>.yaml`); with no finalizer, the deployed resources stay in place as ordinary objects (they keep a harmless tracking annotation).
