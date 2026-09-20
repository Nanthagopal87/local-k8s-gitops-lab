# Argo CD Fundamentals (Phase 3)

Argo CD installed on the single-node k3s cluster from [docs/setup/k3s.md](../setup/k3s.md), with one small Git-managed application. Everything here was observed on this cluster on 2026-09-20; the decisions behind it are in [ADR-002](../decisions/ADR-002-argocd-install-and-sync-policy.md).

## Contents

1. [Summary](#1-summary)
2. [Installation](#2-installation)
3. [Architecture and components](#3-architecture-and-components)
4. [Git integration and the Application object](#4-git-integration-and-the-application-object)
5. [Desired state, live state, sync and health](#5-desired-state-live-state-sync-and-health)
6. [Manual vs automated sync](#6-manual-vs-automated-sync)
7. [Demonstration: what we did and saw](#7-demonstration-what-we-did-and-saw)
8. [Local UI and API access](#8-local-ui-and-api-access)
9. [Resource footprint](#9-resource-footprint)
10. [Observations specific to this environment](#10-observations-specific-to-this-environment)
11. [Limitations of this local lab](#11-limitations-of-this-local-lab)
12. [Production translation](#12-production-translation)
13. [Verify, troubleshoot, remove](#13-verify-troubleshoot-remove)

---

## 1. Summary

```text
Git (public GitHub repo, branch main)
   |   desired state: argocd/apps/argocd-demo/*.yaml
   v
Argo CD  (repo-server renders, application-controller compares and syncs)
   |   reconcile
   v
Kubernetes (namespace argocd-demo: Deployment + Service)
```

| Item | Value |
|---|---|
| Argo CD version | **v3.5.3** (official release, non-HA) |
| Kubernetes / k3s | `v1.36.4+k3s1` (Argo CD 3.5 lists Kubernetes 1.36 as tested) |
| Repository | `https://github.com/Nanthagopal87/local-k8s-gitops-lab.git`, branch `main`, **public**, read anonymously |
| Application | `argocd-demo`, path `argocd/apps/argocd-demo` (a flat directory when this phase was done; **since Phase 5 a Kustomize base + overlays, path `argocd/apps/argocd-demo/overlays/dev`**, see [kustomize.md](../kubernetes/kustomize.md)), destination namespace `argocd-demo` |
| Sync policy | **Manual** (no automated sync, self-heal or prune) |
| Access | `kubectl port-forward` to `https://localhost:8090` (not exposed through nginx or Traefik) |
| Footprint | about 256 MiB across the four running Argo CD Pods (measured) |

## 2. Installation

**Source of truth for the method:** the official Argo CD documentation, [Getting Started](https://argo-cd.readthedocs.io/en/stable/getting_started/), and the release `v3.5.3` on GitHub (2026-09-14, stable, not a pre-release). The docs recommend the `install.yaml` manifest applied with server-side apply, and recommend pinning a version.

**Why it is done this way:**

* **Non-HA `install.yaml`.** `ha/install.yaml` is for production resilience and far too heavy here; `core-install.yaml` has no UI or API server.
* **Server-side apply** is required because the CRDs are too large for the annotation that client-side apply needs.
* **Pinned tag** (`v3.5.3`), not `stable`, so the installation is reproducible. The upstream manifest's sha256 at install time began `7efe2d6bbc03f636...`.
* **Kustomize wrapper** ([`argocd/install/kustomization.yaml`](../../argocd/install/kustomization.yaml)) so the install is declarative and in Git. It uses the upstream manifest unmodified except for three patches that scale **Dex**, the **notifications controller** and the **ApplicationSet controller** to 0 replicas, because this lab uses none of them yet (no SSO, no notifications, ApplicationSets are a later phase). Their CRDs and Services remain, so re-enabling one is trivial.

Before installing a component, CLAUDE.md asks for four answers:

| Question | Answer |
|---|---|
| Why is it needed? | It is the GitOps controller; without it there is nothing to reconcile Git to the cluster. |
| What does it provide? | Continuous comparison of Git and cluster, sync, health, history, and an API/UI. |
| Resource impact? | Measured about 256 MiB working set for the four running Pods (a pre-install guess of 0.6-1.0 GiB was too pessimistic). See [section 9](#9-resource-footprint). |
| Lighter alternative? | `core-install.yaml` (no UI/API) or scaling more components down. Not chosen because the UI and API are the point of the exercise. |

Commands:

```bash
# render/validate first
kubectl kustomize argocd/install | head
kubectl apply -f argocd/install/namespace.yaml
kubectl apply -k argocd/install --server-side --force-conflicts --dry-run=server
# install
kubectl apply -k argocd/install --server-side --force-conflicts
kubectl get pods -n argocd -w
```

Result: 60 objects applied (3 CRDs, 6 Deployments, 1 StatefulSet, 8 Services, 7 NetworkPolicies, RBAC, ConfigMaps, Secrets). All four running Pods were Ready about **95 seconds** after applying, of which about 54 seconds was pulling the Argo CD image. No Pod restarted.

Startup warnings, all inside the first ~10 seconds and gone afterwards (after 15:07:45 all four components logged zero warnings or errors):

| Message | Cause |
|---|---|
| `Error: secret "argocd-redis" not found` (server and controller Pods) | The `argocd-redis` Secret is generated by Redis's init container; the other Pods started first. Kubernetes retried the container; no restart occurred. |
| `Unable to parse updated settings: server.secretkey is missing` (controller) | The controller read settings before the API server wrote its signing key on first start. |
| `redis: ... connection refused` / `i/o timeout` | Redis was not up yet (it logged "Ready to accept connections" at 15:07:41). |
| `GHCR webhook secret is not configured`, `Static assets directory ... does not exist` | Benign: no webhooks configured; embedded UI assets are used. |

## 3. Architecture and components

```text
                   +--------------------------+
 browser / API --> |      argocd-server       | --- Redis (cache)
 (port-forward)    |  API + UI, authn/authz   |
                   +-----------+--------------+
                               | records requests on Application objects
                               v
   Git  <----  argocd-repo-server  <----gRPC----  argocd-application-controller ----> Kubernetes API
 (HTTPS)      clones, renders, caches                (reconciliation loop)             (read live, apply)
                       |                                        |
                       +----------- Redis (manifest cache) -----+
```

What is actually installed and running here:

| Component | Kind | What it does | Talks to | Git polling? | Reconcile loop? |
|---|---|---|---|---|---|
| **argocd-application-controller** | StatefulSet | Watches `Application` objects and the live resources in the destination cluster; asks the repo-server for the desired manifests of the current Git revision; **diffs** desired vs live; sets sync and health status; **executes syncs**. Runs with wide (`*`) cluster permissions. | Kubernetes API, repo-server (gRPC 8081), Redis | Drives it: it asks the repo-server to check Git on the reconciliation timer | **Yes, this is the loop** |
| **argocd-repo-server** | Deployment | The only component that touches Git. Clones/fetches the repo (`git fetch origin --tags --force --prune` seen in its logs), **renders** manifests (plain YAML here; Kustomize or Helm later) and caches the result. Stateless. | Git over HTTPS, Redis | **Yes, it performs the fetches** | Supplies the "desired" side |
| **argocd-server** | Deployment | API server and web UI; authentication (local `admin` here) and RBAC. A sync clicked in the UI or sent to the API becomes a request the controller carries out. | Kubernetes API (reads/writes Application objects and settings), repo-server, Redis | No | No (it is the front door, not the engine) |
| **argocd-redis** | Deployment | In-memory cache (manifest and app-state caches) with a generated password; persistence disabled (`--save "" --appendonly no`). Losing it only costs a cache rebuild. | nothing outbound | No | Supports it |
| argocd-dex-server | Deployment, **0 replicas** | Optional SSO/OIDC broker. Not needed for the local `admin` login. | n/a | n/a | n/a |
| argocd-applicationset-controller | Deployment, **0 replicas** | Generates many Applications from templates. A later phase. | n/a | n/a | n/a |
| argocd-notifications-controller | Deployment, **0 replicas** | Sends notifications on app events. Not used yet. | n/a | n/a | n/a |

Custom resources (installed CRDs): `Application`, `AppProject`, `ApplicationSet`. Seven NetworkPolicies ship with the install and k3s enforces them.

The reconciliation flow:

```text
Git repository (revision N)
      |  repo-server fetches + renders   (on the reconciliation timer, or on refresh)
      v
Desired state (rendered manifests, cached in Redis)
      |
      v
Application controller  <---- watches ---- live state in the Kubernetes API
      |  compare
      +--> equal   : sync status = Synced
      +--> differs : sync status = OutOfSync   (nothing changes unless a sync runs)
      |
      +--> health check of the live objects : Healthy / Progressing / Degraded / Missing ...
      |
      +--> on sync (manual or automated): apply desired state to the Kubernetes API
```

Two different triggers keep it current, and we measured both: **watches** on live objects (drift noticed in about **1 second**) and **Git polling** on a timer (a new commit noticed after **236 seconds**).

## 4. Git integration and the Application object

An **Application** is a custom resource that says: *watch this path in this repo at this revision, and keep this namespace in that cluster equal to it.* [`argocd/applications/argocd-demo.yaml`](../../argocd/applications/argocd-demo.yaml):

```yaml
spec:
  project: default
  source:
    repoURL: https://github.com/Nanthagopal87/local-k8s-gitops-lab.git
    targetRevision: main
    path: argocd/apps/argocd-demo
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd-demo
  # no syncPolicy: manual sync
```

* Argo CD reads the **public** repo anonymously over HTTPS. No credential exists for it anywhere (none is in Git, none is in a Secret).
* The manifests it deploys lived in `argocd/apps/argocd-demo/` (Namespace, Deployment with 2 replicas, ClusterIP Service) when this phase was done; they are now `argocd/apps/argocd-demo/base/` plus `overlays/dev` and `overlays/prod` (Phase 5, [kustomize.md](../kubernetes/kustomize.md)), and `argocd-demo` now runs 1 replica in the `dev` overlay. The demonstrations below describe the earlier flat layout as it happened. Argo CD never reads a local filesystem path; it clones from GitHub.
* The Application object itself was applied once with `kubectl` (a bootstrap step). Making Argo CD manage its own Applications from Git is a later topic.
* Argo CD marks what it deploys with the annotation `argocd.argoproj.io/tracking-id`, which is how it knows an object belongs to `argocd-demo`.
* Only changes under the Application's `path` matter. Commits elsewhere in the repo move the tracked revision but change no manifests, so the app stays `Synced`.
* The `default` AppProject is unrestricted. Scoped AppProjects and RBAC are later topics.

## 5. Desired state, live state, sync and health

* **Desired state**: the manifests rendered from Git. **Live state**: what is in the cluster.
* **Sync status** answers "do they match?": `Synced`, `OutOfSync`, `Unknown`.
* **Health** answers "is what is running working?": `Healthy`, `Progressing`, `Degraded`, `Suspended`, `Missing`, `Unknown`. Argo CD derives it from the objects (for a Deployment: are the replicas ready).
* They are **independent**. We saw both `OutOfSync + Missing` (nothing deployed yet), `Synced + Progressing` (rolling out), `Synced + Healthy`, and `OutOfSync + Healthy` (running fine but not what Git says).
* **Sync** is the act of applying desired state to the cluster. **Reconciliation** is the continuous compare-and-correct loop around it.
* Each sync is recorded in the Application's history (`id` and Git revision), which is what makes going back to a revision possible.

## 6. Manual vs automated sync

| | Manual (chosen) | Automated |
|---|---|---|
| Detects drift and Git changes | Yes, shows `OutOfSync` | Yes |
| Changes the cluster | Only when a sync is requested | Automatically on Git change |
| Self-heal | No: a hand edit stays until synced | Optional `selfHeal`: reverts hand edits |
| Prune | Not unless requested | Optional `prune`: deletes what left Git |

Why manual first: this exercise exists to *see* the states and the moment of reconciliation, and automated sync would hide them. Self-heal and prune also add risk (deleting things, fighting with humans) that is better introduced once the manual workflow is understood. CLAUDE.md lists them as later topics. Switching later is a small change (`syncPolicy.automated`), decided in Git.

## 7. Demonstration: what we did and saw

All steps ran against the public repo. Times are measured.

| # | Step | What happened |
|---|---|---|
| 1 | Applied the `Application` (`kubectl apply -f argocd/applications/argocd-demo.yaml`) | Within seconds: **`OutOfSync` / `Missing`** against commit `08d15fe`, all three resources `OutOfSync`. The repo-server logs show a `git fetch`. **Nothing was created**: the `argocd-demo` namespace did not exist (manual policy). |
| 2 | Manual sync (API call, same as the UI's Sync button) | About **12 s** later: **`Synced` / `Healthy`**, Namespace, Deployment (2/2) and Service created; health passed through `Progressing`. History id 0. |
| 3 | **Drift**: `kubectl scale deployment argocd-demo --replicas=1` (deliberate, temporary) | Argo CD marked the Deployment **`OutOfSync` after about 1 s** (it watches live objects), still `Healthy`. The API showed `spec.replicas`: Git target **2**, live **1**. 30 s later still drifted: no self-heal. |
| 4 | **Reconcile**: manual sync | About **11 s**: live replicas back to **2**, `Synced` / `Healthy`. Operation `Succeeded: successfully synced (all tasks run)`. |
| 5 | **Git change**: edit `replicas: 2` to `3` in the manifest, commit, push | Argo CD noticed the new commit by itself **236 s after the push** (polling): `OutOfSync` against `0b2c834`, cluster still at 2. |
| 6 | Sync | About **12 s**: **3/3** Pods Ready, `Synced` / `Healthy`, traffic spread across all three. |
| 7 | **Rollback the GitOps way**: `git revert`, push, API **refresh** | The refresh made Argo CD check Git **immediately** (`OutOfSync` after ~5 s, versus 236 s by polling); a sync brought it back to 2 replicas in ~14 s. History now has three entries. |

```text
Git desired state (replicas: 2)  !=  live (replicas: 1)
        |
        v
Argo CD detects drift (~1 s)  ->  OutOfSync
        |
        v
manual sync  ->  reconciled  ->  live == Git  ->  Synced
```

**`kubectl` change vs Git change:**

| | `kubectl scale` | Edit, commit, push |
|---|---|---|
| Source of truth | Nobody's: the cluster now differs from Git | Git |
| Recorded / reviewable | No (only in cluster state) | Yes: commit, diff, author, history |
| Argo CD's view | Drift: `OutOfSync` | A new desired state: `OutOfSync` until synced, then `Synced` |
| Reversible | Only by hand | `git revert`, then sync |
| Right for | One-off troubleshooting | Every real change |

Note the drift step was the only imperative change to the cluster in this phase, and it was restored. Nothing used `kubectl edit`.

## 8. Local UI and API access

```text
Windows browser -> https://localhost:8090 -> kubectl port-forward (127.0.0.1) -> argocd-server
```

1. In a normal WSL terminal, keep it open:
   ```bash
   kubectl port-forward svc/argocd-server -n argocd 8090:443 --address 127.0.0.1
   ```
2. Browse to `https://localhost:8090` on Windows. Accept the certificate warning (Argo CD's own self-signed certificate).
3. Log in as **`admin`**. The initial password is in the Secret `argocd-initial-admin-secret` (key `password`). Read it in your own terminal, and never paste or commit it:
   ```bash
   kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
   ```
4. Stop with Ctrl+C.

Notes:

* **Why 8090, not the documented 8080:** nginx's `/keycloak` upstream is `localhost:8080`; a forward there would hijack it. Port 8090 was free.
* **Why it works from Windows:** the forward is a real listening socket, which WSL's localhost forwarding carries. (NodePorts and `hostPort`s do not work that way; see [ADR-001](../decisions/ADR-001-traefik-alternate-host-ports.md).) The listener is bound to `127.0.0.1` only. Verified: `/healthz` returns 200 from WSL and from Windows `curl.exe`, and the UI root returns the Argo CD page.
* **Login verified without exposing the password:** an API login returned a session token; the same list-applications call returns 401 without a token.
* **Not exposed** through nginx, Traefik, a NodePort or a LoadBalancer, and nothing on ports 80/443 or 8880/8843 changed.
* The API is the same one the UI uses (`/api/version`, `/api/v1/applications`, `/api/v1/session`, `.../sync`), which is how the syncs above were triggered.
* No `argocd` CLI is installed (see ADR-002).

## 9. Resource footprint

| | Before install (15:04) | After install + demo (15:21) |
|---|---|---|
| Used | 5.1 GiB | 5.5 GiB |
| Available | 2.4 GiB | **2.0 GiB** |
| Swap used | 786 MiB | **981 MiB** (of 2 GiB) |
| Node (`kubectl top`) | 398m CPU, 77% memory | 536m CPU, 79% memory |

Argo CD Pods (working set, `kubectl top`): application-controller **177 MiB** (it was only 23 MiB right after install and grew as it built its caches during the syncs), repo-server **45 MiB**, server **30 MiB**, redis **4 MiB**, total about **256 MiB**. The demo app's Pods use about 1m CPU and under 1 MiB each. The upstream manifest sets no requests or limits, so nothing caps these.

The pre-install estimate (0.6-1.0 GiB) was too high; measured usage was much lower. The node-level increase (about 0.4 GiB used, 0.2 GiB more swap) is larger than the Pod totals because it also includes k3s/containerd growth from the new CRDs and images and page cache; k3s plus containerd resident memory was about 1.36 GiB at the end, up from about 0.93 GiB in Phase 1. Headroom is now about 2.0 GiB with swap about half used: fine for this lab, but shrinking. Trim further by leaving the three controllers scaled down, or stop k3s when idle.

## 10. Observations specific to this environment

* **Startup ordering races are normal** (see the warning table in [section 2](#2-installation)). Wait for readiness and read the timestamps before deciding something is broken.
* **Drift is noticed in about 1 second, new Git commits in minutes.** Detection of live changes uses Kubernetes watches; Git changes use polling (about 3 minutes plus jitter; we measured 236 s). A refresh (`?refresh=normal` on the API, or the UI's Refresh) checks Git immediately. Webhooks would remove the delay, but GitHub cannot reach a WSL instance.
* **`kubectl port-forward` is a foreground process.** Stopping it by Ctrl+C in its terminal is cleanest. If it is started as an orphan, find it with `ss -ltnp | grep 8090` and `kill <pid>`; note that `pkill -f` with a pattern matching your own command line can kill the shell running it.
* **Argo CD uses annotation tracking** (`argocd.argoproj.io/tracking-id`), not the older `app.kubernetes.io/instance` label.
* **The upstream install also creates NetworkPolicies** for its own Pods; k3s enforces them. The port-forward path and the in-cluster paths used here worked with them.
* **The GitHub API briefly reported the default branch as `master`** right after the first push to the empty repository, then `main`.
* **The public repository exposes the docs.** Machine-specific details in older docs (hostname, an internal IP, a username) are public now.

## 11. Limitations of this local lab

* Single node and single replica of everything: no HA, no failover.
* Manual sync only; no self-heal, prune, sync waves or sync windows.
* One application, the unrestricted `default` project, and a local `admin` account with no SSO. The initial admin Secret still exists.
* Public repository, so nothing secret can be managed through it; no external secrets integration.
* When this doc was written Argo CD managed only `argocd-demo`. Since Phase 4 it also manages `k8s-learning` and the Traefik `HelmChartConfig` (`traefik-config`); see [gitops-adoption.md](gitops-adoption.md). The fake Secret and the disposable demos remain outside Argo CD.
* Self-signed TLS, access only by port-forward, no ingress, no webhooks, no notifications, no metrics stack.
* No resource requests/limits on Argo CD, and the controller holds cluster-wide permissions.

## 12. Production translation

| Here | In a production GKE / platform setup |
|---|---|
| Non-HA `install.yaml`, 1 replica each | HA install (multiple server/repo-server/controller shards, Redis HA), resource requests and limits, PodDisruptionBudgets, node placement |
| Local `admin` login and the initial Secret | Disable the local admin; SSO through Dex or the IdP (for example Google Workspace/OIDC); RBAC by group |
| Default `AppProject`, one Application | AppProjects per team/environment limiting repos, namespaces and cluster resources; ApplicationSets for many apps/environments/clusters |
| Public repo, anonymous read | Private repos with a read-only deploy key or GitHub App; credentials managed as Secrets from a secrets manager |
| Manual sync | Automated sync with self-heal for lower environments; controlled/manual or windowed sync for production; prune with care |
| Polling every ~3 minutes | Webhooks from the Git provider (plus polling as a fallback) |
| `kubectl port-forward` on `localhost` | Ingress or Gateway with TLS (for example cert-manager) and a real DNS name, behind SSO; API not public |
| Cluster-wide controller permissions | Same in-cluster, but multi-cluster setups use scoped credentials per cluster; Workload Identity instead of static keys |
| Application applied by hand once | "App of apps" or ApplicationSets so Argo CD manages its own Applications (and itself) from Git |
| No notifications or metrics | Notifications to chat/incident tools; Prometheus metrics and dashboards |
| Pinned tag, manual upgrade | Pinned versions, staged upgrades with CRD handling, tested compatibility with the GKE version |

## 13. Verify, troubleshoot, remove

Verify:

```bash
kubectl get pods,svc -n argocd
kubectl get statefulset,deploy -n argocd
kubectl get application -n argocd                       # SYNC STATUS / HEALTH STATUS columns
kubectl get application argocd-demo -n argocd -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}'
kubectl top pods -n argocd
```

Troubleshoot:

| Symptom | Check |
|---|---|
| `https://localhost:8090` does not load | Is the forward running (`ss -ltnp \| grep 8090`)? Restart it in a terminal. Is `argocd-server` Ready? |
| Browser certificate warning | Expected: Argo CD's self-signed certificate |
| `OutOfSync` that you did not expect | `kubectl get application argocd-demo -n argocd -o yaml` (status), and compare Git with the live object. Manual policy never corrects it by itself. |
| App does not notice a Git push | Wait for polling (about 3-4 min) or refresh: UI Refresh, or `GET /api/v1/applications/argocd-demo?refresh=normal` |
| `ComparisonError` / repo unreachable | `kubectl logs -n argocd deploy/argocd-repo-server`; is the repo public and the URL correct; does the cluster have outbound internet? |
| Pod stuck at start with `secret "argocd-redis" not found` | Usually resolves by itself once Redis's init container has run; check `kubectl get events -n argocd` |
| Argo CD Pods evicted / slow, node under pressure | `free -h`, `kubectl top nodes`; keep Dex/notifications/ApplicationSet scaled to 0; stop k3s when idle |

Remove (destructive; see ADR-002):

```bash
kubectl delete -f argocd/applications/argocd-demo.yaml    # the Application only; deployed resources stay (no finalizer)
kubectl delete namespace argocd-demo                      # the demo workload
kubectl delete -k argocd/install                          # Argo CD, its CRDs and ALL Applications
```
