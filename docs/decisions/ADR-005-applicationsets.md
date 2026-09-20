# ADR-005: ApplicationSets with isolated per-environment namespaces

## Status

Accepted and implemented on 2026-09-20 (Phase 6). Retiring the hand-made `argocd-demo` Application in favour of generated ones is **not decided** and is deferred (it needs deleting an Application).

## Context

Phase 6 teaches Argo CD ApplicationSets on the existing Argo CD, Git and Kustomize lab. Constraints: no new infrastructure, manual sync only, never delete an existing Application, never let two Applications manage the same Kubernetes resources, and keep the memory footprint small (about 2 GiB available on a 7.5 GiB WSL2 VM).

Facts found by inspection: the ApplicationSet CRD, RBAC, Service and NetworkPolicy were installed, but the controller was scaled to 0 by a Phase 3 patch. The existing Application `argocd-demo` already owns live resources in namespace `argocd-demo`, and both of its Kustomize overlays (`dev`, `prod`) produce a Namespace `argocd-demo` and objects inside it. Any Application generated from those overlays would therefore claim the same resource identities.

## Decision

1. **Enable the existing ApplicationSet controller** by removing its Phase 3 scale-to-0 patch in `argocd/install/kustomization.yaml` (declarative, in Git). A server-side diff of the whole install showed this was the only change. Dex and the notifications controller stay scaled to 0.
2. **Use the list generator** with two elements, `dev` and `prod`, and a Go template (`goTemplate: true`, `missingkey=error`) that generates `argocd-demo-<environment>`.
3. **Isolate every generated Application in its own namespace** using two thin Kustomize overlays, `overlays/isolated/dev` and `overlays/isolated/prod`, that layer on the existing overlays and only set the namespace (and rename the Namespace object). The existing overlays and the existing Application are unchanged.
4. **Keep the hand-made `argocd-demo` Application.** It continues to own `argocd-demo` resources; nothing was deleted or transferred.
5. **Deploy the ApplicationSet from Git** through a small Application, `applicationsets` (manual sync, `directory.include: '*.yaml'`), so Git is the source of truth for the ApplicationSet too. The generated Applications are derived objects owned by the ApplicationSet.
6. **Guard against controller-driven deletion**: `syncPolicy.applicationsSync: create-update` (the controller never deletes generated Applications) and `preserveResourcesOnDeletion: true`.
7. **Manual sync for everything.** The template has no `syncPolicy`; automated sync, self-heal and prune stay off on all six Applications. Generating an Application and syncing it remain separate actions.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Generate Applications directly on `overlays/dev` and `overlays/prod` and sync only one | Two Applications would render the same identities (shared-resource warnings and ambiguity), which violates the ownership rule. Rejected. |
| Retire `argocd-demo` and let generated Applications take over its namespace | Requires deleting an Application (forbidden here) and an ownership hand-over. Documented as a possible future step. |
| Change the existing overlays to use per-environment namespaces | Would move the live `argocd-demo` resources, meaning recreation. Rejected. |
| Inject the namespace with `spec.source.kustomize.patches` in the ApplicationSet template | Works but mixes manifest concerns into the template and blurs the layering. Rejected in favour of Kustomize overlays. |
| Apply the ApplicationSet with `kubectl` only | Leaves Git out of the ownership model. Rejected; a hand-bootstrapped Application deploys it from Git, like the other Applications. |
| Start with a Git, matrix or cluster generator | More moving parts than needed to learn the concept. Deferred. |
| Enable `create-delete` / default `sync` policy | Lets a list edit delete Applications. Rejected for now. |

## Consequences

Positive:

* The ApplicationSet to Application to Kustomize to Kubernetes chain is demonstrable end to end with no ownership collision (23 resource identities, none claimed twice), no change to any existing workload, and negligible cost (the controller uses about 26 MiB; node memory was unchanged).
* Adding an environment is one line in Git (one list element), verified by a full controlled cycle.
* The layering is clean: the ApplicationSet decides how many Applications and with what parameters, Kustomize decides what they deploy, Argo CD compares and syncs.

Negative / to remember:

* **Duplication in the lab:** the demo workload now also runs from `argocd-demo-dev` and `argocd-demo-prod` in addition to `argocd-demo`. It exists only to avoid collisions.
* **Generated Applications cannot be hand-edited.** The controller reverts spec and metadata edits (annotations and labels too) within about a second. Change the template or generator data in Git.
* The hand-made `argocd-demo` remains a manually managed Application; the target of "only generated Applications" is deferred.
* An extra controller now runs continuously (about 26 MiB) on a memory-tight VM.
* `applicationsSync` and `preserveResourcesOnDeletion` are configured but their deletion paths were not exercised.
* The Application that deploys the ApplicationSet is itself bootstrapped by hand.

Rollback: re-add the scale-to-0 patch for `argocd-applicationset-controller` in `argocd/install/kustomization.yaml` and re-apply (stops generation; existing generated Applications remain until deleted). The isolated overlays and the ApplicationSet can be removed by reverting the Phase 6 commits and deleting the objects deliberately.

## Phase 7 addendum (2026-09-20)

* The Git directory generator, deferred above, was added as a **separate** ApplicationSet (`phase7-git-generator`) for its own application family; the Phase 6 ApplicationSet, its two generated Applications and the hand-made `argocd-demo` were **not** changed. See [ADR-006](ADR-006-gitops-automation-policy.md) and [applicationsets.md](../argocd/applicationsets.md#16-the-git-directory-generator-phase-7).
* The deletion paths that were "configured but not exercised" (`applicationsSync`, removing an input) were exercised on the **Git-generator** ApplicationSet only: with `create-update` a removed directory does not delete its Application; with `sync` it does, and (no finalizer) the workload stays. They remain unexercised for `argocd-demo-environments`.
* Decisions above stay in force. Retiring `argocd-demo` is still **not decided**.

