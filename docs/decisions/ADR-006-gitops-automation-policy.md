# ADR-006: GitOps automation policy for the lab (automated sync, self-heal, prune) and the Git generator

## Status

Accepted and implemented on 2026-09-20 (Phase 7). **This is a policy for this lab only.** It was derived from one sandbox on one single-node cluster and must not be read as an organization-wide policy. The orphaned-ConfigMap handling policy for the existing Applications ([ADR-004](ADR-004-kustomize-base-overlays.md)) and the retirement of the hand-made `argocd-demo` ([ADR-005](ADR-005-applicationsets.md)) remain **open**.

## Context

Until Phase 6 every Application used manual sync ([ADR-002](ADR-002-argocd-install-and-sync-policy.md)), so drift and Git changes were visible but never applied automatically. Phase 7 had to learn the difference between automated sync, self-heal and prune, and how an ApplicationSet discovers Applications from Git, **without** enabling automation on the existing, valuable resources.

Facts from the experiments ([automation.md](../argocd/automation.md), [applicationsets.md](../argocd/applicationsets.md)):

* **Automated sync** deployed a Git change with no human action (153 s after the push, on natural polling), but **did not correct live drift**: a manual `kubectl scale` stayed for the 95 s observed.
* **Self-heal** reverted a manual drift in about 1.4 s, touching only the drifted field.
* **Prune** deleted exactly the resources that disappeared from Git (an orphaned hash-named ConfigMap, then a deliberately removed ConfigMap). Without prune, an automated Application containing an orphan stays `OutOfSync`.
* An ApplicationSet with `applicationsSync: create-update` does **not** delete a generated Application when its Git directory is removed; with `sync` it does, and (with no finalizer) the workload stays behind, orphaned but intact.

## Decision

1. **Automation is enabled on exactly one Application: `phase7-sandbox`**, with `automated: {selfHeal: true, prune: true}`. It is isolated: its own namespace, no PVC, no Secret, no Traefik ownership, no shared resources, and its Namespace object carries `Prune=false,Delete=false`. It stays on as a permanent learning fixture; the policy is changed by editing its Git file and applying it.
2. **Every other Application stays on manual sync.** Reasons:

   | Application | Stays manual because |
   |---|---|
   | `k8s-learning` | Holds a PVC and RBAC, and lives next to a deliberately manual fake Secret; prune and self-heal would act on objects with data or on the boundary defined in ADR-003 |
   | `traefik-config` | Manages only the `HelmChartConfig` of the k3s-owned Traefik; an automatic change or revert reconfigures the ingress controller for everything behind it |
   | `argocd-demo` | Hand-made, still owns the Phase 5 hash-name orphan question (ADR-004, open); prune would settle that policy by accident, and it is also the Application whose retirement is undecided |
   | `argocd-demo-dev`, `argocd-demo-prod` | Generated from a template that intentionally has no `syncPolicy`; changing this changes every generated Application, and it shares the same orphan question |
   | `p7g-service-a-dev`, `p7g-service-b-dev` | Same rule: the Git-generator template has no `syncPolicy`. Generating an Application and deploying it are separate decisions |
   | `applicationsets` | It deploys the ApplicationSets themselves; self-heal or prune there could revert or remove the objects that create other Applications |

3. **The three switches are decided per Application, never globally.** Before any Application gets automation in this lab, all of these must be true and recorded: it owns its resources exclusively (checked across all Applications), it holds nothing persistent, its Namespace and any PVC carry `Prune=false,Delete=false`, the prune set was listed (`requiresPruning`) before prune was enabled, and the kill switch (remove the `automated:` block in Git and apply) is known.
4. **ApplicationSet deletion behaviour stays conservative.** The Git-directory ApplicationSet `phase7-git-generator` uses `applicationsSync: create-update` and `preserveResourcesOnDeletion: true`, like ADR-005. The temporary switch to `sync` used in the removal experiment was reverted the same session (commits `a0cc433` then `8eeba03`).
5. **The Git generator lives in its own ApplicationSet** for its own application family (`argocd/apps/phase7-git-generator/`), so the Phase 6 ApplicationSet and every existing Application are untouched.
6. **Rollback is `git revert`,** not `kubectl rollout undo`, because prune can delete a ConfigMap that an old ReplicaSet still references.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Enable automation on all Applications | Rejected. Puts a PVC, RBAC and the ingress configuration under self-heal and prune on evidence from a stateless busybox demo |
| Enable automated sync but never self-heal or prune | Kept as the first step of the experiment only. Its `OutOfSync`-forever behaviour with orphans (section 6 of automation.md) makes it a poor end state for generated ConfigMaps |
| Put `syncPolicy.automated` in the ApplicationSet template | Rejected for now. It changes every generated Application at once |
| Per-resource protections on the sandbox workload (`Prune=false`, `ignoreDifferences`) | Not needed for a disposable sandbox; they are the normal way to protect an important workload under prune and would be needed before automating anything real |
| Use `IgnoreExtraneous` or disable Kustomize hash suffixes to avoid orphans | Rejected here on purpose (out of scope; would change the Phase 5 approach). The sandbox produced evidence instead |
| A Git file generator or matrix generator first | Deferred; the directory generator is the smallest example of "a folder becomes an Application" |
| Delete the leftover objects after the removal experiment | Not needed: reverting the removal made the regenerated Application re-adopt the running workload with no recreation |

## Consequences

Positive:

* The behaviour of all three switches is proven with timings and identity evidence, at zero risk to the existing workloads (their UIDs, PVC and HelmChartConfig generation are unchanged from the baseline).
* Automation is isolated and reversible: one file and one `kubectl apply`.
* The lab has a Git-driven "folder to Application" example for later phases (Backstage-style scaffolding of a directory).

Negative / to remember:

* **The sandbox is a live, self-healing, self-pruning Application.** A bad commit to `argocd/apps/phase7-sandbox/` deploys or deletes by itself. Its blast radius is intentionally tiny.
* **Manual `kubectl` edits to `phase7-sandbox` are reverted in about a second.** Turn self-heal off in Git first.
* **Detection is still polling** (153-344 s observed; two timers stack for the ApplicationSet), because no webhook can reach a local laptop.
* **The Git generator adds a naming contract:** the overlay's `namespace:` and the ApplicationSet's destination namespace must both be `p7g-<service>-<environment>`. Nothing enforces it.
* **More Applications and pods:** a second ApplicationSet, three Applications and four small pods (busybox, 16 MiB limits) are now running; node memory was effectively unchanged (used 5667 to 5671 MiB).
* **Untested:** a bad commit under automation, the Namespace guardrail, finalizer cascade on Application deletion, retry and backoff, sync windows. See automation.md section 12.

Rollback: remove the `automated:` block from `argocd/applications/phase7-sandbox.yaml` in Git and `kubectl apply -f` it; delete the sandbox Application and namespace by hand if it is no longer wanted. To remove the Git-generator family, revert the commits that added `argocd/applicationsets/phase7-git-generator.yaml` and `argocd/apps/phase7-git-generator/`, sync the `applicationsets` Application, then delete the generated Applications and the `p7g-*` namespaces deliberately.
