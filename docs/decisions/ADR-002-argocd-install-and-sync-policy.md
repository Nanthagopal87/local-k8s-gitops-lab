# ADR-002: Argo CD install method, repository access and sync policy

## Status

Accepted and implemented on 2026-09-20 (Phase 3).

## Context

The lab needs a GitOps controller on a single-node k3s cluster inside WSL2 with about 7.5 GiB of RAM (roughly 2.4 GiB available and swap already in use before this phase). Goals: install Argo CD, understand it, and demonstrate `Git -> Argo CD -> Kubernetes` with one tiny application, without introducing advanced features early (CLAUDE.md sections 5, 12 and 25).

Facts gathered from the official documentation and the release itself:

* Latest stable release: **v3.5.3** (2026-09-14). Argo CD 3.5 lists Kubernetes 1.36 (this cluster) as tested.
* The official install is `manifests/install.yaml` applied with **server-side apply** (the CRDs are too large for client-side apply). The docs recommend pinning a version instead of tracking `stable`.
* `install.yaml` is the non-HA install (`ha/install.yaml` is separate). `core-install.yaml` has no API server or UI.
* `install.yaml` v3.5.3 contains 6 Deployments and 1 StatefulSet at one replica each, and sets no resource requests or limits.

## Decision

1. **Install the official `install.yaml` pinned to `v3.5.3`, non-HA**, into the `argocd` namespace, through a small Kustomization ([`argocd/install/`](../../argocd/install/kustomization.yaml)) applied with `kubectl apply -k argocd/install --server-side --force-conflicts`. The upstream manifest is used unmodified apart from three patches (below).
2. **Scale three unused features to 0 replicas** with declarative patches: `argocd-dex-server` (SSO), `argocd-notifications-controller` and `argocd-applicationset-controller`. Their CRDs and Services remain installed, so re-enabling one is a one-line change. This saves memory and honours "no enterprise SSO / no ApplicationSets yet".
3. **Repository access: a public GitHub repository over HTTPS with no credentials.** The Application points at `https://github.com/Nanthagopal87/local-k8s-gitops-lab.git`, branch `main`. Argo CD needs no repository credentials, and no secret exists to leak.
4. **Sync policy: manual.** The Application deliberately has no `syncPolicy.automated`. Argo CD detects drift and Git changes and shows `OutOfSync`, but changes the cluster only when a sync is requested. Automated sync, self-heal and prune are deferred.
5. **Access: `kubectl port-forward` bound to `127.0.0.1` on local port 8090.** Argo CD is not exposed through nginx or Traefik. Port 8090 is used instead of the documented 8080 because nginx's `/keycloak` upstream is `localhost:8080`.
6. **No `argocd` CLI.** The UI, the REST API and `kubectl` are enough for this phase, and the CLI binary is about 250 MB. The initial admin password is read from the `argocd-initial-admin-secret` Secret.
7. **Default `AppProject`** for now. Scoped AppProjects are a later topic.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Unmodified full `install.yaml` | Simplest and closest to upstream, but runs three components that this lab does not use. Rejected in favour of the trimmed variant; reverting is trivial. |
| `core-install.yaml` | Lightest, but has no API server or UI, so it cannot show the UI or be driven through the API. Rejected. |
| `ha/install.yaml` | Multiple replicas and Redis HA. Far too heavy for a single-node 7.5 GiB VM and explicitly out of scope. |
| Helm chart from a community repository | Not the official install manifest, and Helm is out of scope for this phase. Rejected. |
| Automated sync with self-heal now | Would hide the drift and `OutOfSync` states that the exercise is meant to show, and adds pruning risk. Deferred until the manual workflow is understood. |
| Expose Argo CD via a Traefik Ingress | Adds ingress, TLS and hostname handling and touches the Phase 2.6 port arrangement. Deferred. |
| Private repository with a deploy key or token | Needs a credential stored as a Secret outside Git. Not needed once a public repository was chosen; revisit for anything sensitive. |
| Install the `argocd` CLI | A large binary for capabilities the UI, API and `kubectl` already provide here. Deferred. |

## Consequences

Positive:

* **Reproducible and pinned:** the whole installation is in Git; upgrading means changing the tag in one file. The upstream manifest checksum recorded at install time was `sha256:7efe2d6bbc03f636...` (v3.5.3).
* **Small footprint:** measured Argo CD working set was about 256 MiB (see `docs/argocd/fundamentals.md`), well under the pre-install estimate.
* **Visible GitOps:** manual sync makes each state (`OutOfSync`, `Synced`, `Progressing`, `Healthy`) observable and forces an explicit sync.
* **No credentials anywhere** for repository access.

Negative / to remember:

* **Everything in the repository is public**, including these docs; machine-specific identifiers in them are public too. Do not put anything sensitive in this repository. Secrets need a separate mechanism before any real credential is involved.
* **Manual sync means drift persists** until someone syncs. That is the point here and the wrong default for real environments.
* **The application controller has cluster-wide `*` permissions** (the standard install grants it), so Argo CD effectively holds cluster-admin on this cluster.
* **The upstream manifest sets no resource requests or limits**, so nothing caps Argo CD's memory on this constrained VM. Watch swap and available memory.
* **`argocd-initial-admin-secret` remains** after the first login. In a real environment change the admin password and delete it, or disable the local admin and use SSO.
* **TLS is Argo CD's own self-signed certificate**, so browsers warn.
* **Polling is not instant:** without a webhook, Git changes are picked up on Argo CD's reconciliation timer (about 3 minutes) unless a refresh is requested. GitHub cannot call a WebSocket-less local WSL instance anyway.

Rollback: `kubectl delete -f argocd/applications/argocd-demo.yaml` removes the Application (its deployed resources stay unless a finalizer is set; none is). `kubectl delete -k argocd/install` removes Argo CD, its CRDs and every Application.
