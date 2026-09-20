# Argo CD Automation: automated sync, self-heal and prune (Phase 7)

What the three automation switches do, how they differ, and what each one costs when it goes wrong, demonstrated on one isolated sandbox Application. Everything marked **(demonstrated)** was run on this cluster on 2026-09-20; **(documented only)** was not exercised. The resulting lab policy is in [ADR-006](../decisions/ADR-006-gitops-automation-policy.md). The Git-generator half of Phase 7 is in [applicationsets.md](applicationsets.md#16-the-git-directory-generator-phase-7).

> Nothing here is an organization-wide recommendation. It is evidence from a small, disposable, single-node lab.

## Contents

1. [Summary](#1-summary)
2. [The three switches](#2-the-three-switches)
3. [The sandbox](#3-the-sandbox)
4. [Experiment A: automated sync only](#4-experiment-a-automated-sync-only)
5. [Experiment B: self-heal](#5-experiment-b-self-heal)
6. [The orphaned ConfigMap under automation](#6-the-orphaned-configmap-under-automation)
7. [Experiment C: prune](#7-experiment-c-prune)
8. [How the switches interact](#8-how-the-switches-interact)
9. [Timing summary and how it was measured](#9-timing-summary-and-how-it-was-measured)
10. [Risks and safeguards](#10-risks-and-safeguards)
11. [What was intentionally not automated](#11-what-was-intentionally-not-automated)
12. [What was not tested](#12-what-was-not-tested)
13. [Production translation](#13-production-translation)
14. [Verify, troubleshoot, remove](#14-verify-troubleshoot-remove)

---

## 1. Summary

```text
Manual sync      Git change  --> OutOfSync --> (a human presses Sync) --> cluster changes
Automated sync   Git change  --> OutOfSync --> (Argo CD syncs)        --> cluster changes
Self-heal        live drift  --> OutOfSync --> (Argo CD syncs)        --> drift reverted
Prune            a manifest disappears from Git --> the live object is DELETED on sync
```

| Item | Value |
|---|---|
| Where automation is enabled | **Only** the Application `phase7-sandbox` (namespace `phase7-sandbox`) |
| Everything else | Manual sync, unchanged: `argocd-demo`, `argocd-demo-dev`, `argocd-demo-prod`, `k8s-learning`, `traefik-config`, `applicationsets`, `p7g-service-a-dev`, `p7g-service-b-dev` |
| How the policy is changed | Edit `argocd/applications/phase7-sandbox.yaml` in Git, push, then `kubectl apply -f` it (the same hand-bootstrap as the other Applications) |
| Argo CD | `v3.5.3`, Kubernetes `v1.36.4+k3s1` |

## 2. The three switches

All three live under `spec.syncPolicy.automated` of an **Application**. (An ApplicationSet has its own, unrelated `syncPolicy`; see [applicationsets.md](applicationsets.md).)

```yaml
syncPolicy:
  automated:          # switch 1: present = automated sync is on
    selfHeal: true    # switch 2: default false
    prune: true       # switch 3: default false
```

| Switch | Reacts to | Effect | It does NOT |
|---|---|---|---|
| **Automated sync** (`automated: {}`) | A **new Git revision** (or an Application spec change) that makes the app `OutOfSync` | Syncs it without a human | Correct live drift. Delete anything |
| **Self-heal** (`selfHeal: true`) | **Live cluster drift** (someone edited or scaled the object) | Syncs again, reverting the drift to the Git state | Delete objects that are no longer in Git |
| **Prune** (`prune: true`) | A resource that is **in the cluster but no longer in the rendered Git output** | Deletes it during an automated sync | Fire on its own: it only widens what an automated sync is allowed to do |

Self-heal and prune are *modifiers* of automated sync, so they only exist inside `automated:`.

**Kubernetes and Argo CD roles.** Kubernetes controllers keep converging the runtime (Deployment to ReplicaSet to Pods). Argo CD compares the rendered Git state with the live objects and decides whether to *apply* or *delete*. These switches configure only the Argo CD side. They never change how Kubernetes itself behaves.

## 3. The sandbox

A dedicated Application, `phase7-sandbox`, so that no existing workload is involved (files: [`argocd/apps/phase7-sandbox/`](../../argocd/apps/phase7-sandbox/), [`argocd/applications/phase7-sandbox.yaml`](../../argocd/applications/phase7-sandbox.yaml)).

| Property | Value |
|---|---|
| Namespace | `phase7-sandbox`, used by nothing else (checked: no other Application lists a resource there) |
| Workload | Deployment `sandbox-web` (busybox httpd, image already cached), Service `sandbox-web` |
| Config | A Kustomize-generated, hash-named ConfigMap (`sandbox-config-<hash>`) and one disposable ConfigMap (`disposable-note`) |
| Persistence | None: no PVC, no Secret, no PV |
| Traefik / Ingress | None |
| Guardrail | The Namespace carries `argocd.argoproj.io/sync-options: Prune=false,Delete=false`, so it can never be a prune candidate (same rule as `k8s-learning`, [ADR-003](../decisions/ADR-003-gitops-ownership-boundaries.md)) |

It was created **manual-sync first** (commit `dbd1801`), applied, and synced by hand (about 10 s to `Healthy`). Each experiment then changed `syncPolicy` in a separate, small Git commit.

## 4. Experiment A: automated sync only

Policy: `automated: {}` (self-heal and prune off). Trigger: `replicas: 1 -> 2` in Git. **(demonstrated)**

| Step | Time | From push |
|---|---|---|
| Commit / push finished | 19:10:08 / 19:10:13 | |
| Argo CD detects the revision and starts the sync (natural polling, no refresh forced) | 19:12:46 | **153 s** |
| Sync finished | 19:12:47 | 154 s |
| `Healthy` (second Pod ready) | 19:12:57 | 164 s |

* The Application's own record shows `initiatedBy: {automated: true}` and `history` lists it next to my earlier manual sync (`initiatedBy: {username: ...}`). **No one pressed Sync.**
* The `OutOfSync` state lasted **less than the 1 s sampling interval**: detection and sync start happened in the same reconcile, so it was never observed as a distinct state.
* The Deployment kept its UID and its ReplicaSet; only `spec.replicas` changed. All other resources reported `unchanged`.

### Control: automated sync does NOT correct drift **(demonstrated)**

With the same policy I ran `kubectl scale deployment sandbox-web --replicas=3` (a temporary manual change in the sandbox):

* `OutOfSync` within **about 1 s** (Argo CD watches the live objects, so drift detection needs no polling).
* **95 s later** the live Deployment still had 3 replicas and the Application was still `OutOfSync`; the last operation timestamp had not moved.

```text
Automated sync = reconcile GIT changes automatically      ("Git changed, so deploy it")
It is NOT        reconcile LIVE drift automatically
```

## 5. Experiment B: self-heal

Policy: `automated: {selfHeal: true}` (commit `861830d`). **(demonstrated)**

1. **Enabling it on the standing drift** (3 live vs 2 in Git): applied at 19:15:56, sync at 19:15:58, `Synced` at 19:16:01. Enabling self-heal on an already-drifted Application heals it at once.
2. **A fresh drift**: `kubectl scale ... --replicas=4` at 19:16:58.7.

| Step | Time |
|---|---|
| Drift created | 19:16:58.7 |
| Corrective sync started and finished (`initiatedBy: {automated: true}`) | 19:16:59 |
| `Synced/Healthy` and back to 2 replicas | by 19:17:00.1 (**about 1.4 s** in total) |

Evidence that only the replica count was touched:

* The Deployment UID was unchanged; its generation went 4 to 6 (scale up, scale down), with **no Pod template change**.
* The ReplicaSet was the same object (scaled 2 to 4 to 2). Its two extra Pods were created and deleted within the same second (Kubernetes events); the **two original Pods were untouched**.
* A before/after snapshot of every Deployment, ReplicaSet, Service, ConfigMap and Namespace in the sandbox shows identical names and UIDs.
* The controller log for the moment shows the normal loop: `Updated sync status: OutOfSync -> Synced`, then `Skipping auto-sync: application status is Synced`.

```text
Automated sync = reconcile Git changes automatically
Self-heal      = reconcile live drift automatically
```

**Consequence to remember:** a legitimate emergency `kubectl` change to a self-healing Application is reverted in about a second. To make a manual change stick you must first turn self-heal off (in Git) or change Git.

## 6. The orphaned ConfigMap under automation

The sandbox uses a Kustomize `configMapGenerator`, so changing a value renames the ConfigMap (`sandbox-config-<hash>`). This is the Phase 5 orphan problem ([ADR-004](../decisions/ADR-004-kustomize-base-overlays.md)). I observed it under the current policy (automated sync + self-heal, **prune off**) by changing `MESSAGE=initial` to `MESSAGE=v2`. **(demonstrated)** A refresh was requested to save waiting, so this timing is refresh-to-sync, not polling.

| Step | Time |
|---|---|
| Refresh requested | 19:18:20.9 |
| Automated sync started | 19:18:23 (2.1 s) |
| New ReplicaSet `Healthy` | 19:18:41 |

* The new ConfigMap was created, the Pod template changed, and Kubernetes rolled the Pods (a new ReplicaSet). One `Unhealthy` readiness event appeared on the *old* Pod as it was replaced.
* The **old ConfigMap stayed** (the orphan), and the Application stayed **`OutOfSync`** for the roughly 2 minutes I watched (until prune was enabled), with exactly one resource `requiresPruning`. Automated sync did not delete it and did not retry: with prune off, "no longer in Git" never means "delete".
* The old, scaled-to-0 ReplicaSet still referenced the orphan; no running Pod did.

So **automation alone does not solve the orphan problem; it only makes the Application reach `OutOfSync` faster.** Section 7 shows what prune does with it.

## 7. Experiment C: prune

### Pre-flight (all checks run before enabling) **(demonstrated)**

| Check | Result |
|---|---|
| Desired resources (rendered from Git HEAD) | Namespace, `disposable-note`, `sandbox-config-<new hash>`, Service, Deployment |
| Live resources | The same, plus the orphan `sandbox-config-<old hash>`, two ReplicaSets, Pods |
| Persistent resources | None (0 PVC, 0 Secrets, 0 PVs) |
| Other Application owns anything in the namespace? | No: only `phase7-sandbox` lists resources there |
| What would prune remove right now (`requiresPruning`)? | **Exactly one**: `ConfigMap/sandbox-config-<old hash>` |
| Anything using it? | No running Pod; only a dormant ReplicaSet at 0 replicas |

No Namespace, PVC or Secret was in the prune set, so no stop condition applied.

### C1: enabling prune sweeps the standing orphan

Policy `automated: {selfHeal: true, prune: true}` (commit `e7e46f6`), applied at 19:20:25. Sync at 19:20:25, `Synced/Healthy` at 19:20:26. Result: `ConfigMap/sandbox-config-<old hash>`: `Pruned`. Every other resource kept its UID. **Prune solved the orphan automatically**, and it did so the moment it was enabled because the app was already `OutOfSync`.

### C2: deliberate removal from Git

Removed the manifest `disposable-note.yaml` (and its line in `kustomization.yaml`); commit and push at 19:21:13.

```text
Git removal --> desired resource disappears --> Argo CD sees it requires pruning --> automated sync --> resource pruned
```

* Natural polling detected it and synced at **19:25:55**, **282 s** after the push. Result: `ConfigMap/disposable-note`: `Pruned`; Namespace, `sandbox-config`, Service and Deployment `unchanged`; identical UIDs before and after for everything else.

## 8. How the switches interact

| Situation | Automated sync only | + self-heal | + prune |
|---|---|---|---|
| New Git revision, resources added or changed | synced | synced | synced |
| Live drift on a managed object | detected, **not** corrected (demonstrated) | corrected in about 1 s (demonstrated) | same |
| Manifest removed from Git | Application stays `OutOfSync`, object stays (demonstrated in section 6) | same | object deleted (demonstrated) |
| Enabling the switch on an already `OutOfSync` app | Syncs immediately (seen with self-heal and prune) | | |
| Kustomize hash-name change | New ConfigMap, Pods roll, old one orphaned | same | old one pruned |

Two more facts observed in the controller log: after a sync at a given revision, later reconciles say `Skipping auto-sync: application status is Synced`, and if another operation is running it says `another operation is in progress` and waits. Argo CD does not sync in a tight loop.

## 9. Timing summary and how it was measured

| What | Measured |
|---|---|
| Push to automated sync start, natural polling | 153 s (A), 282 s (C2); earlier phases 230-286 s. Polling is a timer with jitter, not a constant |
| Refresh to automated sync start | about 2-3 s |
| Live drift to `OutOfSync` | about 1 s (watch-based) |
| Live drift to corrected, self-heal | about 1.4 s |
| Sync itself | 1-2 s; `Healthy` about 9-18 s later, depending on the Pod |

Method: a read-only shell loop sampled the Application and Deployment once per second and logged every state change, and I read `operationState` and `history` from the Application itself (its `initiatedBy` field is what proves a sync was automated). One-second sampling cannot see states shorter than a second, which is why `OutOfSync` appears only as "under 1 s". Timings of hops that used a forced refresh are labelled as such.

## 10. Risks and safeguards

| Switch | Useful when | Risk | Safeguard used here |
|---|---|---|---|
| Automated sync | Merging to Git is already reviewed and the workload is disposable or low-risk | **A bad merged commit deploys itself**, with no human between merge and cluster | Sandbox only; small commits; Git revert as the rollback |
| Self-heal | Git must stay authoritative and manual drift must not persist | **Emergency manual fixes are reverted in about a second** | Sandbox only; procedure to turn it off (section 14) |
| Prune | Removing a manifest from Git should remove the object | **A bad Git deletion becomes a cluster deletion** (an accidental `git rm`, a wrong path in a `kustomization.yaml`, a broken render) | Isolated namespace, no persistent data, pre-flight check of the prune set, Namespace guardrail annotation |

A further prune consequence, **reasoned but not exercised**: once the old hash-named ConfigMap is pruned, an old ReplicaSet that referenced it (`kubectl rollout undo` to that revision) would fail to start its Pods. The rollback that works is a Git revert, which renders the old ConfigMap name again.

## 11. What was intentionally not automated

* **Every existing Application** stays manual: `argocd-demo`, `k8s-learning`, `traefik-config` and `applicationsets` (hand-made), and `argocd-demo-dev`, `argocd-demo-prod` (generated). Reasons are in [ADR-006](../decisions/ADR-006-gitops-automation-policy.md).
* **The generated Applications** of both ApplicationSets: their templates deliberately have no `syncPolicy`.
* **No global default.** There is no ApplicationSet template or AppProject setting that enables automation broadly.
* **The Phase 5 orphan policy was not changed.** No hash-suffix change, no global `IgnoreExtraneous`, no prune on `argocd-demo`. The sandbox only produced evidence.
* Ingress, PVC, Secrets, nginx and Traefik configuration were not touched.

## 12. What was not tested

* **A bad commit under automation** (a broken manifest or an unintended deletion reaching the cluster on its own). Only intentional, safe changes were made.
* **The `Prune=false,Delete=false` Namespace guardrail**: present, but the Namespace was never a prune candidate, so it was not exercised.
* **Sync failure, retry and backoff** (`syncPolicy.retry`), and `allowEmpty`.
* **Sync windows, `ApplyOutOfSyncOnly`, `PruneLast`, per-resource `Prune=false` on workloads.**
* **Automation on stateful or shared resources** (PVCs, the Traefik `HelmChartConfig`, RBAC).
* **Notifications** (the controller is scaled to 0) and **webhooks** (none can reach this laptop, so detection is polling).

## 13. Production translation

| In this lab | In a platform setting |
|---|---|
| Automation on one sandbox, decided by hand | A per-environment policy: typically automated sync and self-heal for lower environments, gated promotion (approval, sync windows) for production |
| Prune only where the blast radius is nothing | Prune usually on, with **`Prune=false` on stateful and shared objects**, `PruneLast`, and PR checks (policy as code) that catch a destructive diff before merge |
| Self-heal reverts every manual edit | Break-glass procedure: an audited way to pause self-heal on one Application |
| Polling (150-300 s) | Webhooks, so a merge reaches the cluster in seconds |
| Rollback = `git revert` | The same, plus Argo Rollouts for a runtime abort during progressive delivery (a later topic) |
| One human decides everything | Review and CODEOWNERS on the repo are the real control over what automation may deploy |

## 14. Verify, troubleshoot, remove

```bash
# Which Applications have automation, and what state are they in?
kubectl get applications.argoproj.io -n argocd \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,POLICY:.spec.syncPolicy

# Was the last sync automated? (initiatedBy shows {automated: true} or a username)
kubectl get application phase7-sandbox -n argocd -o jsonpath='{.status.operationState.operation.initiatedBy}{"\n"}'

# What would prune delete? (resources flagged requiresPruning)
kubectl get application phase7-sandbox -n argocd -o jsonpath='{range .status.resources[?(@.requiresPruning==true)]}{.kind}/{.name}{"\n"}{end}'

# Controller decisions
kubectl logs -n argocd argocd-application-controller-0 --since=10m | grep phase7-sandbox | grep -i auto-sync
```

| Symptom | Check |
|---|---|
| `OutOfSync` that automated sync never fixes | Is the only difference a resource that `requiresPruning` while prune is off (section 6)? |
| A manual `kubectl` change disappears | Self-heal reverted it (section 5). Change Git, or turn self-heal off first |
| An object vanished after a Git change | Prune. Check `git log -p` of the path and the last operation's `syncResult` |
| Automation seems to do nothing | Wait for polling (up to about 5 minutes), or request a refresh: `kubectl annotate application <name> -n argocd argocd.argoproj.io/refresh=normal --overwrite` |

**Kill switch (any Application):** remove the `automated:` block in its Git file, push, and `kubectl apply -f` it. Objects stay as they are; the Application is manual again.

**Remove the sandbox (destructive; not done):** `kubectl delete -f argocd/applications/phase7-sandbox.yaml` removes the Application only (no finalizer, so the workload stays); then delete the `phase7-sandbox` namespace by hand, and revert the Git files.
