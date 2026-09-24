# ADR-007: Atlantis as a local learning experiment (Kubernetes-hosted, manually triggered)

## Status

Accepted and implemented on 2026-09-24.

## Context

Atlantis is the standard Git-PR-driven `plan`/`apply` automation tool for Terraform/OpenTofu, and was named as a possible future infrastructure-lane component in the project's earlier discussion of a future Enterprise Engineering Platform. Two things needed a decision before installing it in this lab, purely as a learning exercise (nothing in this lab or the related EEP discovery work requires it):

* **Deployment shape.** A standalone host binary versus a Kubernetes workload deployed through Argo CD. The user asked specifically for the latter: "my expectation atlantis hosted in k8s via ArgoCD."
* **Trigger mechanism.** Atlantis's normal model is a real webhook, pushed from GitHub/GitLab into Atlantis whenever a PR/MR event occurs. This WSL2 lab has no public inbound reachability — the same limitation already documented for Argo CD's own Git polling (ADR-002: "GitHub cannot call a WebSocket-less local WSL instance anyway"). Switching Git host (GitHub vs GitLab) does not change this: the blocker is the lab machine's network position, not which provider is used. A tunnel (ngrok/Cloudflare Tunnel) would fix it but would be the first thing in this lab ever exposed to the public internet, which the user did not ask for.
* Atlantis has **no standalone local-CLI mode**: `atlantis plan`/`atlantis apply` typed at a terminal are PR-comment commands parsed by a running server from a webhook, not a direct command against a local directory (confirmed against an open, unresolved upstream feature request for exactly that capability, `runatlantis/atlantis#671`).

## Decision

1. **Deploy Atlantis as an ordinary Kubernetes Deployment, reconciled by Argo CD**, using the same hand-bootstrapped `Application` pattern as every other Application in this lab (`argocd/applications/atlantis.yaml` → `argocd/apps/atlantis/`). Manual sync, no `syncPolicy`, consistent with every Application here except the isolated `phase7-sandbox` experiment.
2. **Official image, pinned:** `ghcr.io/runatlantis/atlantis:v0.48.0` (the unsuffixed/Alpine variant, which already bundles OpenTofu — no custom build). `ATLANTIS_DEFAULT_TF_DISTRIBUTION=opentofu` selects it over the bundled Terraform.
3. **Trigger by manually simulated webhook**, not a real one: `kubectl port-forward` to Atlantis's Service (the same pattern already used for the Argo CD UI), then `curl` a hand-built GitHub `pull_request`/`issue_comment` payload at `/events`. No tunnel, no public exposure.
4. **Dummy GitHub credentials (`gh-user`/`gh-token` = `fake`/`fake`)** — Atlantis's own documented pattern for local setup with no real GitHub API calls. This lab's public repository is cloned anonymously (the same unauthenticated `git clone` Argo CD itself already relies on for this repo), so no real credential is needed at all.
5. **The dummy token stays outside Argo CD's management**, applied once by hand (`argocd/apps/atlantis/manual/secret.yaml`, deliberately not listed in `kustomization.yaml`). This directly follows the precedent already set in ADR-003 for the Phase 2 fake demo Secret: even a credential with zero real value should not be brought under GitOps management, to avoid normalizing a path a real credential could later follow by accident.
6. **Persistent storage** (`local-path`, 1Gi, `Prune=false,Delete=false`) rather than `emptyDir`, following Atlantis's own documented recommendation (it stores checked-out repo state and plan output on disk; losing it on every Pod restart is avoidable at negligible cost on this VM's 930 GB of free disk).
7. **The toy Terraform/OpenTofu content lives in a new top-level directory, `atlantis-demo/`**, using only the `hashicorp/local` provider (creates one local file inside Atlantis's own checked-out working copy; no cloud account, no credentials, no billing, no real infrastructure). It is deliberately **not** referenced by any Argo CD Application — Atlantis clones and acts on it through its own, separate Git integration, entirely independent of the GitOps application-delivery flow the rest of this repository demonstrates. This mirrors the lane separation (application delivery vs. infrastructure provisioning) discussed earlier in this project's architecture review.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Host binary in `~/.local/bin` (the originally proposed shape) | Simpler and fully disposable, but the user explicitly wants the Kubernetes/Argo CD-hosted experience, which is also more consistent with this lab's stated purpose. Superseded by this ADR before implementation began. |
| Real webhook via a tunnel (ngrok / Cloudflare Tunnel) | Would exercise the real end-to-end mechanism, but is the first public-internet exposure this lab would ever have. Not requested; deferred as a possible future, explicitly-approved step. |
| A real (scoped, read-only) GitHub PAT instead of a dummy token | Not needed: this repository is public, so anonymous clone already works, and no PR-comment or status-check call is expected to succeed or be needed for this experiment. Avoids a real credential existing anywhere in this lab for a purely disposable exercise. |
| `emptyDir` instead of a PVC | Simpler, but contradicts Atlantis's own documented guidance and this lab already has an established PVC pattern (`demo-data`) to follow instead. |
| StatefulSet instead of Deployment | Atlantis's own docs suggest it for stable per-replica identity, relevant only above one replica. Deployment is equivalent here and consistent with every other workload in this lab. |
| Terraform instead of OpenTofu | OpenTofu is already the IaC tool this lab (and the related EEP work) has settled on; using Terraform here would add a second tool for no reason, and the bundled image supports OpenTofu natively. |

## Consequences

Positive:

* Atlantis becomes a real, observable GitOps citizen: `Synced`/`Healthy` status, sync history and drift detection all work on it exactly as they do for every other Application, which is the actual learning goal.
* No public exposure, no real credentials, no real infrastructure, and nothing that could be billed or misused if the dummy token ever leaked (it has no value).
* Fully reversible and fully isolated from the rest of the lab: a dedicated namespace, no shared resources with `argocd-demo`, `k8s-learning`, `traefik-config` or any Phase 6/7 Application.

Negative / to remember:

* **The webhook is simulated, not real.** This experiment does not prove that a real GitHub-delivered webhook would reach this lab — it deliberately can't, and isn't trying to. Getting the simulated payload's exact shape right is expected to take iteration, informed by Atlantis's own logs.
* **Atlantis may still attempt live GitHub API calls** (for example to list changed files in the "PR") that a fake token cannot authenticate. If so, some part of the plan/apply cycle may need to be driven more directly rather than purely through the simulated webhook; this is a known open risk, not yet resolved at the time this ADR was written.
* Atlantis adds a new, independent Git-cloning identity to this lab (separate from Argo CD's own clone of the same repository) — harmless here since both are anonymous and read-only against a public repo, but worth remembering if this repository ever stops being public.
* `atlantis-demo/` is intentionally undocumented to any Argo CD Application; a future contributor unfamiliar with this ADR could mistake it for orphaned content. The `atlantis-demo/README.md` and this ADR are the pointers back to each other.

Rollback: `kubectl delete -f argocd/applications/atlantis.yaml` removes the Application (its deployed resources stay, since it has no finalizer); the `atlantis` namespace and PVC would then need deleting by hand, since Argo CD never prunes them. Revert the Git commits that added `argocd/apps/atlantis/`, `argocd/applications/atlantis.yaml` and `atlantis-demo/`.
