# Atlantis learning experiment

What this directory is for, and how it differs from everything else in this repository.

## What this is

A single trivial OpenTofu module (`main.tf`, `hashicorp/local` provider only) that Atlantis
plans and applies. It is **not** part of the Argo CD / GitOps application-delivery flow that
the rest of this repository demonstrates. Atlantis clones this repository directly (its own
Git integration, independent of Argo CD) and runs `tofu plan`/`tofu apply` against this one
directory when triggered.

## Why `hashicorp/local`

It creates one local file inside Atlantis's own checked-out working copy and nothing else.
No cloud account, no credentials, no billing, no real infrastructure — the same
"prove the mechanism, zero real cost" pattern used for early infrastructure-as-code
validation elsewhere. Safe to `apply` repeatedly.

## Why this is separate from the GitOps app-delivery lab

Application delivery here (`argocd/apps/...`, the ApplicationSets) is one lane: Git desired
state -> Argo CD -> Kustomize -> Kubernetes. Atlantis represents a conceptually **different**
lane — infrastructure provisioning triggered by a Git PR/MR — that a future platform would
keep separate from application delivery (see the architecture-review discussion). Keeping the
toy module in its own top-level directory, untouched by any Argo CD Application, keeps that
boundary visible even though both lanes happen to live in the same repository for this
learning lab.

## How it is triggered

Atlantis itself is deployed *as* a Kubernetes workload, through Argo CD, like everything else
in this lab (see `argocd/apps/atlantis/` and `argocd/applications/atlantis.yaml`). But Atlantis
has no public webhook reachability from GitHub (the same WSL2/NAT limitation documented for
Argo CD's own Git polling in `docs/decisions/ADR-002-argocd-install-and-sync-policy.md`), so it
is triggered by **manually simulated GitHub webhook payloads**, sent with `curl` through a
`kubectl port-forward`, instead of a real GitHub-delivered webhook. Documented in
`docs/decisions/ADR-007-atlantis-local-learning-setup.md`.

## Credentials

Atlantis is configured with dummy `gh-user`/`gh-token` values (`fake`/`fake`), Atlantis's own
documented pattern for exactly this situation. No real GitHub API calls are authenticated;
public, anonymous `git clone` is all that is needed to fetch this public repository.
