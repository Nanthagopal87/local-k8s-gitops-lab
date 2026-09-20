# GitOps Adoption of Existing Resources (Phase 4)

How the manually applied Phase 2 resources and the Traefik configuration were brought under Argo CD **without recreating anything**, where the ownership boundaries are, and what was observed. Builds on [Argo CD fundamentals](fundamentals.md) and [ADR-003](../decisions/ADR-003-gitops-ownership-boundaries.md). All results were observed on this cluster on 2026-09-20.

## Contents

1. [Objective](#1-objective)
2. [What existed, in Git and in the cluster](#2-what-existed-in-git-and-in-the-cluster)
3. [Adoption strategy](#3-adoption-strategy)
4. [Ownership boundaries](#4-ownership-boundaries)
5. [The k8s-learning Application](#5-the-k8s-learning-application)
6. [The traefik-config Application](#6-the-traefik-config-application)
7. [Secret boundary](#7-secret-boundary)
8. [Manual sync behaviour](#8-manual-sync-behaviour)
9. [Drift demonstration](#9-drift-demonstration)
10. [Git-driven change](#10-git-driven-change)
11. [Git revert](#11-git-revert)
12. [Automated sync, self-heal and prune: why they stay off](#12-automated-sync-self-heal-and-prune-why-they-stay-off)
13. [GitOps flow](#13-gitops-flow)
14. [Validation results](#14-validation-results)
15. [Lessons learned](#15-lessons-learned)
16. [Limitations](#16-limitations)

---

## 1. Objective

Move the Phase 2 resources and the Traefik configuration into Git + Argo CD management, keeping the working environment intact, and learn:

* **adoption**: taking over a live object without replacing it;
* **ownership**: Argo CD should own *intentional configuration boundaries*, not every object that happens to exist;
* **drift detection and reconciliation**, and what "manual sync" means in practice.

Target:

```text
Git -> Argo CD -+-> argocd-demo      (Phase 3)
                +-> k8s-learning     (Namespace, Deployment, Service, Ingress, ConfigMap, PVC, RBAC)
                +-> traefik-config   (one HelmChartConfig)
                                        |
                                        v  read by the k3s Helm controller, not by Argo CD
                                     Traefik -> ServiceLB
```

## 2. What existed, in Git and in the cluster

Verified at the start (not assumed from the previous report):

* Git: `main` in sync with the public GitHub remote; `argocd/apps/argocd-demo/` and its Application existed; the Phase 2 manifests were under `kubernetes/learning/`; the Traefik `HelmChartConfig` was under `kubernetes/platform/traefik/`.
* Cluster: Argo CD `v3.5.3` with `argocd-demo` `Synced/Healthy` and **no** sync policy; every Phase 2 resource, the fake Secret, and the Traefik chain running; nginx on 80/443 and Traefik on 8880/8843.
* Before any change, `kubectl diff` of the Phase 2 manifests against the cluster was empty (identical): everything in Git was already pure desired state.

Who had written the live objects (field managers): every Phase 2 object and the `HelmChartConfig` were written by `kubectl` client-side apply only. Some also had runtime writers that Git must not copy: the `traefik` controller writes the Ingress address, the k3s controllers write the PVC provisioning annotations and the Deployment `revision`/status. Fields such as `uid`, `resourceVersion`, `generation`, `creationTimestamp`, `managedFields`, `status`, `clusterIP` and `volumeName` are never copied into manifests.

## 3. Adoption strategy

```text
inspect live objects  ->  compare Git vs live  ->  understand every difference  ->  create Application
      ->  observe (do not sync)  ->  manual sync  ->  prove nothing was recreated
```

1. **Snapshot identities** (UID, ReplicaSet, Pods, PVC, the bound PV, generation) before touching anything.
2. **Compare**: `kubectl diff -f` (server-side dry-run) plus Argo CD's own field-level comparison. The only differences were metadata annotations: Argo CD's `tracking-id` on every object, and one guardrail annotation (below) on the Namespace and the PVC. No spec difference anywhere. Stop conditions (immutable-field errors, replacement, PVC change) were checked and none applied.
3. **Organise**: `git mv` the adopted manifests into `argocd/apps/k8s-learning/` (one source of truth); leave the manual exceptions in `kubernetes/learning/`.
4. **Create the Application** and only observe. Result: `OutOfSync` on all 9 resources, **because they were not yet tracked**, not because anything differed. Nothing in the cluster changed.
5. **Sync manually** with `prune: false`. Result in about 6 s: `Synced`/`Healthy`; every resource reported `configured`.
6. **Prove it**: compare identities with the snapshot.

| Check | Before | After |
|---|---|---|
| UIDs of Namespace, Deployment, Service, Ingress, ConfigMap, PVC, ServiceAccount, Role, RoleBinding, Secret | recorded | **identical** |
| PVC bound to the same PV; PV UID | recorded | **identical**, PVC still `Bound` |
| ReplicaSets and Pods (names, UIDs, restarts) | 2 Pods, 0 restarts | **identical**, 0 restarts |
| Events in `k8s-learning` during the sync | n/a | none (a recreate would show `Killing`/`Created`/`Scheduled`) |
| Deployment `generation` | 8 | 9 (see below) |

The generation change is explained: Argo CD wrote only annotations (its field manager touched **zero** `spec` fields). A throwaway Deployment showed that the API server bumps `generation` for an annotation-only change (1 to 2) but not for a label-only change. It is not a rollout: ReplicaSets and Pods are untouched.

**Guardrail annotation.** Data-bearing objects carry `argocd.argoproj.io/sync-options: Prune=false,Delete=false` (the Namespace, because it also holds objects Argo CD does not own, and the PVC, which holds data). Argo CD can then never delete them, even if pruning is enabled later. It is metadata only and was one of the two intended differences.

## 4. Ownership boundaries

The important lesson: **Argo CD should manage the configuration we author, at the smallest boundary that expresses our intent, and not every object that exists.**

```text
Git
 |
 v
Argo CD  ----owns---->  HelmChartConfig/traefik          (kubernetes/platform/traefik/helmchartconfig.yaml)
                              |  read by
                              v
               k3s Helm controller  ----->  HelmChart/traefik      owner: k3s Addon (manifest on the node, root-owned)
                                                   |  helm release
                                                   v
                                    Traefik Deployment + Service   owner: Helm (managed-by: Helm)
                                                   |  Service type LoadBalancer
                                                   v
                                    ServiceLB DaemonSet svclb-traefik-* + Pods   owner: k3s service controller
```

Ownership was read from the live objects, not assumed:

| Object | Owner evidence | In Git? | Managed by Argo CD? |
|---|---|---|---|
| `HelmChartConfig/traefik` | only field manager: `kubectl-client-side-apply`; no owner markers | yes | **yes** |
| `HelmChart/traefik`, `traefik-crd` | `owner-gvk: k3s.cattle.io/v1 Kind=Addon`, `owner-name: traefik`, `managed-by: helm-controller` | no | **no** |
| Traefik `Deployment`, `Service` | labels `managed-by: Helm`, release `traefik` | no | **no** |
| `DaemonSet svclb-traefik-*` and Pods | owner marker `Service/traefik` (created by the service controller) | no | **no** |
| Phase 2 adopted set (9 objects) | field manager `kubectl-client-side-apply` (plus runtime writers) | yes | **yes** (`k8s-learning`) |
| `Secret/demo-web-secret` | fake learning Secret | yes, outside the Argo CD path | **no** (see section 7) |
| Disposable Pod, NodePort Service, `pvc-writer` Pod | demos | yes, outside the Argo CD path | **no** |

Boundaries are drawn by **directory**: everything in `argocd/apps/k8s-learning/` is Argo CD's; `kubernetes/learning/` holds the manual exceptions (documented in its README); the Traefik path is restricted with `directory.include: helmchartconfig.yaml`.

## 5. The k8s-learning Application

[`argocd/applications/k8s-learning.yaml`](../../argocd/applications/k8s-learning.yaml): source `argocd/apps/k8s-learning` on `main` of the public repo, destination namespace `k8s-learning`, project `default`, **no `syncPolicy`**.

It owns nine objects: Namespace `k8s-learning`, ConfigMap `demo-web-config`, Deployment `demo-web` (2 replicas), Service `demo-web`, Ingress `demo-web`, PVC `demo-data`, ServiceAccount, Role and RoleBinding `pod-reader`. Files: `namespace.yaml`, `demo-web-config.yaml`, `demo-web-deployment.yaml`, `demo-web-service.yaml`, `demo-web-ingress.yaml`, `demo-data-pvc.yaml`, `pod-reader-rbac.yaml`.

## 6. The traefik-config Application

[`argocd/applications/traefik-config.yaml`](../../argocd/applications/traefik-config.yaml): source `kubernetes/platform/traefik` with `directory.include: helmchartconfig.yaml`, destination namespace `kube-system`, **no `syncPolicy`**.

* Argo CD tracks **exactly one resource**: `HelmChartConfig/kube-system/traefik`. It has no idea the `HelmChart`, `Deployment`, `Service` or ServiceLB objects exist (none carries a tracking annotation).
* The only difference before the sync was Argo CD's `tracking-id` annotation. The `valuesContent` (`exposedPort` 8880 and 8843) was identical.
* Because this Application changes what k3s's Helm controller reads, I ran the sync with a **continuous probe** (nginx `:80` and the Traefik Ingress on `:8880` every 0.4 s, 118 samples over about 52 s). Result: **0 interruptions**.
* Chain before/after: the only object that changed was the `HelmChartConfig` (its `resourceVersion`, for the annotation). `HelmChart`, `Deployment`, `Service`, the ServiceLB `DaemonSet` and both Pods were identical; Traefik had 0 restarts and `generation=1`; the k3s Helm controller did not even re-run its job (an annotation is not a values change).
* Not exercised: a *values* change flowing through the Helm controller (Argo CD, HelmChartConfig, Helm controller, Traefik). It would move Traefik's ports, which the task said not to change. The link is verified statically (ownership and boundary), not by changing ports.

Networking after the sync: nginx `:80` returns `301`, `:443` presents `CN=localhost`; Traefik `:8880` and `:8843` route the Phase 2 Ingress; no Pod holds hostPort 80/443.

## 7. Secret boundary

`demo-web-secret` stays outside Argo CD on purpose:

* It is an obviously **fake** learning Secret (`demo-user` / `not-a-real-password`).
* Kubernetes Secret data is **base64-encoded, not encrypted** just because it sits in a Secret object; anyone who can read the Secret or the file can decode it.
* The lab has **no secrets-management solution** (no Sealed Secrets, SOPS, External Secrets or Vault), and none was added in this phase.
* **Real credentials must never be committed to Git.** Putting even a fake Secret under Git-driven sync would set a path that a real one could follow.
* Its manifest remains in `kubernetes/learning/secret/`, applied by hand.

Verified: the Secret has no Argo CD tracking annotation, is not among the 9 resources of `k8s-learning`, is not under `argocd/`, and is still consumed by the Argo CD-managed Deployment (Pods healthy, `DEMO_USER` present). A real secrets approach is a separate future decision.

## 8. Manual sync behaviour

With `syncPolicy` absent:

| Event | What Argo CD does |
|---|---|
| Application created | Compares Git and cluster, reports `OutOfSync`/`Synced` and health. **Changes nothing.** |
| Live object edited by hand | Notices within about 1 s, reports `OutOfSync`. Does not correct it. |
| New commit on `main` | Notices via polling (about 4 minutes measured) or an immediate refresh; reports `OutOfSync`. Does not apply it. |
| Someone requests a sync | Applies Git's desired state (here with `prune: false`). |

Syncs in this phase were requested through the Argo CD API (the same call as the UI's Sync button) with `prune: false`, no force and no replace. Sync status and health are independent: `OutOfSync` with `Healthy` was the normal state during the drift demo.

## 9. Drift demonstration

`kubectl scale deployment demo-web -n k8s-learning --replicas=1` (deliberate and temporary):

| Step | Result |
|---|---|
| Git says / cluster says | replicas 2 / 1 |
| Detection | **about 1 s**, `OutOfSync` (only `Deployment/demo-web`), still `Healthy`. Argo CD's diff: `spec.replicas`: Git `2`, live `1` |
| Self-heal (disabled) | none: still drifted after 40 s |
| Manual sync | **about 13 s**: 2 replicas, `Synced`/`Healthy` |

Side effect worth knowing: scaling down and back up replaced one of the original Phase 2 Pods with a new one. Drift has real consequences, and sync restores the *count*, not the *identity*.

## 10. Git-driven change

`replicas: 2` to `3` in `argocd/apps/k8s-learning/demo-web-deployment.yaml`; reviewed with `git status`/`git diff`/`git diff --cached`; committed and pushed to `main` (a normal commit).

| Step | Result |
|---|---|
| Argo CD notices by itself (polling, no refresh) | **230 s after the push**: `OutOfSync` against the new commit; cluster still 2 |
| Manual sync | **about 17 s**: 3/3 Ready, `Synced`/`Healthy`; all three Pods answered through Traefik |

## 11. Git revert

`git revert` created a new commit restoring `replicas: 2` (history is preserved; nothing was rewritten or force-pushed). After the push, an API refresh made Argo CD check Git immediately (**about 4 s** instead of about 230 s), and the manual sync returned the app to 2 replicas and `Synced`/`Healthy` in about 12 s. This is the GitOps rollback: **change the desired state in Git**, not `kubectl rollout undo`.

```text
Git = desired state          Argo CD = reconciliation engine          Kubernetes = runtime state
```

## 12. Automated sync, self-heal and prune: why they stay off

All three Applications are manual, and they stay manual until you approve a change. This lab exists to *see* the states; automation would hide them, and each setting has consequences worth understanding first.

| Setting | What it does | Practical implication here |
|---|---|---|
| **Automated sync** | `Git change -> Argo CD -> automatic deployment` (within the polling interval) | Git becomes the deploy button, and a bad commit ships by itself. For `traefik-config` it is the riskiest: an edit that moves Traefik back to 80/443 would shadow nginx with no human in the loop. |
| **Self-heal** | `manual cluster drift -> Argo CD -> automatic restoration` | Every hand edit is reverted within seconds, so the drift demo would vanish and `kubectl` troubleshooting changes would be undone. Good for enforcing Git, bad when you are debugging. |
| **Prune** | `resource removed from Git -> Argo CD -> resource deleted from the cluster` | Removing a file deletes the object. Guardrails already exist: the Namespace and the PVC have `Prune=false,Delete=false`, and objects Argo CD does not track (the Secret, the demos) are never pruned. |

If it is enabled later, the least risky order is: automated sync (without self-heal or prune) on `argocd-demo`, then self-heal, then prune, one application at a time, leaving `traefik-config` manual longest. **No change will be made without your explicit approval.**

## 13. GitOps flow

```text
                     commit + push (normal commits)
 you ------------------------------------------------> GitHub (public repo, main)
                                                          ^
                                                          | git fetch (polling ~4 min, or refresh)
                                                          |
                                             argocd-repo-server (renders manifests)
                                                          |
                                                          v
                                    argocd-application-controller: desired (Git) vs live (cluster)
                                     |   watches the cluster (drift seen in ~1 s)
                                     +--> Synced / OutOfSync, Healthy / Progressing / Degraded / Missing
                                     |
              manual sync (UI/API) --+--> apply to the Kubernetes API  ------> argocd-demo, k8s-learning
                                                                       \-----> HelmChartConfig -> k3s Helm controller -> Traefik
```

## 14. Validation results

| Check | Result |
|---|---|
| Applications | `argocd-demo`, `k8s-learning`, `traefik-config`: **all `Synced`/`Healthy`**, all with no sync policy |
| Resources per app | 3, 9 and 1 |
| Recreation during adoption | none (UIDs, ReplicaSets, Pods, PVC and PV identical) |
| PVC | `demo-data` `Bound`, same volume |
| Ingress | `demo-web` still routes through Traefik on `:8880` (HTTP) and `:8843` (HTTPS) |
| nginx | `:80` returns `301`, `:443` presents `CN=localhost`; config hashes and PID unchanged |
| Traefik | ports `8880`/`8843`; Deployment `generation=1`, 0 restarts; ServiceLB hostPorts `8880`/`8843`; no Pod holds 80/443 |
| Secret | untracked by Argo CD, still consumed, not under `argocd/` |
| Health | no unhealthy Pods |
| Memory | used 5.4 GiB, available 2.1 GiB, swap about 990 MiB, unchanged from the start of the phase; Argo CD Pods about 290 MiB in total |
| Git | normal commits only, no rewrite or force-push; secret-pattern and identifier scans clean; the Argo CD admin password appears nowhere in the repository or its history |

## 15. Lessons learned

1. **Adoption is stamping ownership, not replacing.** `OutOfSync` on a freshly created Application over existing objects can simply mean "not yet tracked". Read the field-level diff before reacting.
2. **Never `kubectl apply` an Argo CD-managed manifest by hand.** Argo CD's tracking annotation is not in the file, and a hand apply removes it. `kubectl diff` on an adopted file also stops being empty for that reason. Use Argo CD's diff.
3. **`generation` is not just spec.** For Deployments an annotation-only change bumps it. Judge "was it recreated?" by UIDs, ReplicaSets and Pods.
4. **Own intent, not generated resources.** Ownership is discoverable (`managed-by`, `objectset.rio.cattle.io/owner-*`, field managers). Managing the Traefik `Deployment` or the ServiceLB objects would have started a fight with Helm and k3s.
5. **Boundaries should be visible and enforced.** A directory per Application, `directory.include` for Traefik, guardrail annotations on data-bearing objects, and a README for the manual exceptions.
6. **Three different clocks:** live drift about 1 s (watches), new Git commits about 230 s (polling), forced refresh about 4 s.
7. **Manual sync makes every change explicit** and shows sync status and health as independent. Drift has real side effects: scaling replaced a Pod.
8. **`git mv` keeps one source of truth.** Copying manifests would have created two.
9. **Probe high-impact syncs.** The continuous probe turned "it seemed fine" into 118 samples with 0 failures.
10. **Old files may still cite moved paths.** The Phase 2 docs' re-apply commands were updated for the new layout.

## 16. Limitations

* Sync is manual, so drift and Git changes wait for a human; there is no webhook (a local WSL instance is not reachable from GitHub), so new commits are noticed only after roughly 4 minutes unless refreshed.
* The fake Secret, the disposable demos and the NodePort/`pvc-writer` manifests are still applied by hand.
* The full `Argo CD -> HelmChartConfig -> Helm controller -> Traefik` chain was verified for ownership and non-interference, but a *values* change was deliberately not pushed through it.
* Applications were created with `kubectl` once (bootstrap). Managing Applications themselves from Git ("app of apps") and ApplicationSets are later topics.
* Only the `default` AppProject is used; one cluster; no multi-environment overlays.
* **Public-repository hygiene is only partial.** Current files use placeholders, but the earlier commits (not rewritten, by instruction) still contain the previous machine-specific values, and commit metadata contains the author email.
* The Argo CD UI is reached only through `kubectl port-forward`.
