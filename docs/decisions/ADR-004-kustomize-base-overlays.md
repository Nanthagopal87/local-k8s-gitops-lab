# ADR-004: Kustomize base + overlays for environment configuration

## Status

Accepted and implemented on 2026-09-20 (Phase 5). One consequence (orphaned generated ConfigMaps under manual sync) was cleaned up once by an approved manual deletion; the standing policy question is still **open**, see "Orphaned generated ConfigMaps".

## Context

The lab needed a way to express per-environment differences (dev, prod) without duplicating manifests, and to understand how Argo CD consumes such configuration. Constraints: no new tooling or controllers (memory is tight), no Helm and no ApplicationSets yet, manual sync only, and nothing already deployed may be recreated. `argocd-demo` was chosen as the learning application because it is small, stateless and has no credentials.

## Decision

1. **Use Kustomize base + overlays**, rendered by the Kustomize built into `kubectl` locally and by Argo CD's own (both v5.8.1). No standalone binary.
2. **Layout:** `argocd/apps/argocd-demo/base/` (common: Namespace, Deployment, Service, generated ConfigMap, namespace transformer) and `overlays/dev`, `overlays/prod` (differences only). `k8s-learning` and Traefik were deliberately **not** converted.
3. **One Application, switched between overlays.** `argocd-demo` reads `overlays/dev` (steady state) and was switched to `overlays/prod` and back by changing `spec.source.path` in Git. There is never a second Application for the same resources.
4. **No name or selector transformations on live resources.** Overlays use `labels` with `includeSelectors: false`, patches for values, an image pin, and `configMapGenerator`. `namePrefix`/`nameSuffix` and the deprecated `commonLabels` are not used.
5. **Two patch styles are shown side by side**: strategic merge (dev) and JSON 6902 (prod).
6. **Refactor first, change second.** The base + an empty dev overlay was proven to render identical to the previous manifests and synced as a no-op before any environment difference was introduced.
7. **Sync stays manual** on every Application; automated sync, self-heal and prune remain off.
8. **No secrets in overlays or generators.** Kustomize is a config composer, not a secrets tool.

## Alternatives Considered

| Option | Verdict |
|---|---|
| A directory of full manifests per environment | Duplicates everything; drift between environments is inevitable. Rejected. |
| Helm | Solves packaging/templating, not "a few differences over my own YAML"; out of scope and adds a templating language. Documented conceptually only. |
| One Application per environment (or ApplicationSets) | Two Applications on the same resource names compete for ownership; safe multi-environment needs separate namespaces or identities. Deferred to a later phase. |
| `commonLabels` for the environment label | Rewrites the immutable selector; a server-side dry run rejected it against the live Deployment. Rejected. |
| `namePrefix`/`nameSuffix` per environment | Renames live objects, which means replacement and orphans. Rejected. |
| Convert `k8s-learning` and Traefik to Kustomize too | Repository churn with no learning value, and Traefik's boundary (ADR-003) should not move. Rejected. |
| Pin a different image tag per environment | Only `busybox:1.37` is cached and no external registry is used. The pin is kept at the same tag to show the mechanism. |

## Consequences

Positive:

* Environment differences are a few reviewable lines; rendering locally previews exactly what Argo CD will apply (same output, same ConfigMap hash).
* Switching environments is a Git change plus a manual sync, observed with a diff first. Existing objects were updated in place (same UIDs) every time.
* Config changes roll Pods because the generated ConfigMap's name carries a content hash.

Negative / to remember:

* **Orphans.** Each distinct generated ConfigMap leaves the previous one in the cluster. With prune disabled, Argo CD reports it as `PruneSkipped (requires pruning)` and the Application stays **`OutOfSync`** on that one object even though everything else is `Synced` and `Healthy` (observed after every switch). Nothing is broken. No running workload and not the current Deployment reference the orphan; only a dormant older ReplicaSet (rollout history, 0 replicas) does, which matters only for a rollback to that revision.
* A generated ConfigMap has no namespace of its own. Without a `namespace:` in the kustomization the reference to its hashed name is not rewritten (found and fixed in this phase).
* Git now holds inputs to a render, so reviews should include the rendered diff.
* Overlays that grow many patches indicate a base that is doing too much.

## Orphaned generated ConfigMaps: one-off cleanup done, standing policy still open

After the dev, prod, dev round trip, `argocd-demo` was on `dev` with one orphan (the prod ConfigMap) and showed `OutOfSync`. **Option 1 was approved and carried out on 2026-09-20**: after positively identifying the single resource Argo CD marked `requiresPruning` and verifying it was not desired, held no sensitive data, and was not referenced by the current Deployment or any running workload, it was deleted with `kubectl delete configmap argocd-demo-config-5bcd24kd97 -n argocd-demo`. **This was a manual deletion, not an Argo CD prune**, and no policy changed: automated sync, self-heal and prune remain off. `argocd-demo` returned to `Synced/Healthy`. No Git change was needed, because the desired state in Git never included that ConfigMap.

The problem will recur after each overlay switch, so the standing choice below remains open and is separate from the automation decision (automated sync / self-heal / prune) from Phase 4. Nothing further has been decided.

| Option | Effect | Notes |
|---|---|---|
| 1. One-off targeted manual deletion of the named orphan (**done once**) | Deletes just that unreferenced ConfigMap; the Application becomes `Synced`. No policy change. | The smallest action; must be repeated by hand after every environment switch. |
| 2. Mark generated ConfigMaps `argocd.argoproj.io/compare-options: IgnoreExtraneous` | Orphans stop affecting sync status. | Documented Argo CD option, not tested here. Orphans then accumulate silently until pruned. |
| 3. Enable `prune` on this Application | Orphans are deleted on each sync. | A policy change that needs approval; the guardrails from ADR-003 (`Prune=false` on data-bearing objects) apply to `k8s-learning`, not to this app. |
| 4. `generatorOptions: disableNameSuffixHash: true` | Stable name, no orphans. | Config changes no longer roll Pods (the Phase 2 problem returns). |
| 5. Keep cleaning by hand (option 1 each time) | Explicit, one object at a time. | The Application shows `OutOfSync` between a switch and the cleanup. |

**Phase 7 evidence (the standing policy is still open; nothing here changes `argocd-demo`).** On an isolated sandbox Application, an automated sync with self-heal but **without** prune left the orphaned hash-named ConfigMap in place and the Application `OutOfSync` for the roughly 2 minutes observed; enabling `prune` on that sandbox deleted exactly the orphan and made it `Synced` (option 3 in practice, on a disposable app only). One consequence not exercised: after the old ConfigMap is pruned, a `kubectl rollout undo` to a ReplicaSet that referenced it would fail; a Git revert is the safe rollback. Details: [automation.md](../argocd/automation.md#6-the-orphaned-configmap-under-automation), [ADR-006](ADR-006-gitops-automation-policy.md).

Rollback for the layout itself: `git revert` the Phase 5 commits and re-apply `argocd/applications/argocd-demo.yaml` (the manifests return to the flat layout, which renders identically).
