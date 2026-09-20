# Argo CD ApplicationSets (Phases 6 and 7)

What an ApplicationSet is, how it generates Applications, and how that sits on top of Kustomize and Argo CD, demonstrated on the lab's `argocd-demo` app. Everything marked **(demonstrated)** was run on this cluster on 2026-09-20; **(documented only)** was not exercised. Decisions are in [ADR-005](../decisions/ADR-005-applicationsets.md). Kustomize itself is covered in [kustomize.md](../kubernetes/kustomize.md). Sections 1-15 are Phase 6 (list generator); **section 16 is Phase 7 (Git directory generator)**. Automated sync, self-heal and prune are in [automation.md](automation.md).

## Contents

1. [Summary](#1-summary)
2. [Application vs ApplicationSet](#2-application-vs-applicationset)
3. [Who answers which question](#3-who-answers-which-question)
4. [Generators and the list generator](#4-generators-and-the-list-generator)
5. [Template and parameters](#5-template-and-parameters)
6. [What was built](#6-what-was-built)
7. [Ownership](#7-ownership)
8. [Enabling the ApplicationSet controller](#8-enabling-the-applicationset-controller)
9. [The lifecycle: separate loops with measured timings](#9-the-lifecycle-separate-loops-with-measured-timings)
10. [Manual sync stays the default](#10-manual-sync-stays-the-default)
11. [GitOps flow](#11-gitops-flow)
12. [Production translation](#12-production-translation)
13. [Limitations of this single-node lab](#13-limitations-of-this-single-node-lab)
14. [Lessons and common mistakes](#14-lessons-and-common-mistakes)
15. [Verify, troubleshoot, remove](#15-verify-troubleshoot-remove)
16. [The Git directory generator (Phase 7)](#16-the-git-directory-generator-phase-7)

---

## 1. Summary

```text
Git
 |  argocd/applicationsets/argocd-demo.yaml
 v
Application "applicationsets"          (hand-bootstrapped once, manual sync)
 |  deploys the ApplicationSet from Git
 v
ApplicationSet "argocd-demo-environments"      list generator: dev, prod
 |  generates (owner reference)
 +--> Application argocd-demo-dev   --> Kustomize overlays/isolated/dev   --> namespace argocd-demo-dev   (1 replica)
 +--> Application argocd-demo-prod  --> Kustomize overlays/isolated/prod  --> namespace argocd-demo-prod  (3 replicas)
```

| Item | Value |
|---|---|
| Argo CD | `v3.5.3`; ApplicationSet controller **enabled in this phase** (it had been scaled to 0 since Phase 3) |
| ApplicationSet | `argocd-demo-environments`, list generator, elements `environment: dev` and `environment: prod` |
| Generated Applications | `argocd-demo-dev`, `argocd-demo-prod` (owned by the ApplicationSet) |
| Sync policy | **manual** for every Application; automated sync, self-heal and prune are all off |
| Existing Application | `argocd-demo` was **not touched**; it keeps its own namespace and resources |
| Controller footprint | about 26 MiB working set (measured) |

## 2. Application vs ApplicationSet

An **Application** describes one deployment:

```text
Application
    |
    v
"Deploy this Git path (repo + revision + path) to this destination (cluster + namespace), with this sync policy."
```

An **ApplicationSet** is a controller-driven template that produces Applications:

```text
ApplicationSet
    |  generators emit parameter sets      (for example: environment=dev, environment=prod)
    v
template  +  each parameter set   =   one Application
    |
    v
each generated Application has its own Git source and destination
```

**Why it exists.** Writing Applications by hand does not scale: ten environments, or a hundred services, means ten or a hundred nearly identical YAML files that must be kept in step (the same repo URL, the same policies, only a name or a path differing). One typo or one forgotten update creates drift *between the Applications themselves*. An ApplicationSet turns "N copies" into "one template plus a list", so a change to the template changes every generated Application, and adding one element adds one Application.

What an ApplicationSet is **not**:

* not a replacement for an Application (it *creates* them, and each still does the deploying);
* not a replacement for Kustomize (it does not render manifests);
* not a deployment engine by itself (it never applies workload manifests to the cluster).

## 3. Who answers which question

| Concept | Responsibility | Question it answers |
|---|---|---|
| Git | Source of truth for desired state | What should exist? |
| **ApplicationSet** | Generates Applications from a template and generator parameters | *How many Applications should exist, and with what parameters?* |
| **Application** | Defines one Git source plus one destination | *Which Git path goes to which destination?* |
| **Kustomize** | Renders and composes Kubernetes manifests | *What manifests does that path deploy?* |
| **Argo CD** | Compares rendered desired state with live state and syncs | *Is the live cluster synchronized with the Git desired state?* |
| **Kubernetes controllers** | Reconcile runtime state (ReplicaSets, Pods, Endpoints) | *How do I make the cluster converge to that desired state?* |

Layering, top to bottom:

```text
ApplicationSet  ->  Application  ->  Kustomize  ->  Kubernetes
```

`ApplicationSet != Kustomize`, `ApplicationSet != Application`, `ApplicationSet != a deployment engine`.

## 4. Generators and the list generator

A **generator** produces parameter sets. The **list generator** is the simplest: a fixed list written in the ApplicationSet itself.

```yaml
generators:
  - list:
      elements:
        - environment: dev
        - environment: prod
```

Each element is a set of key/value parameters (`environment=dev`, then `environment=prod`), and the template runs once per element. That is all a generator is. Others (Git directories or files, clusters, pull requests, matrix, merge) differ only in *where the parameter sets come from* (see [section 12](#12-production-translation)). They were deliberately not used here. **(documented only)**

## 5. Template and parameters

Parameters are substituted into the Application template. This lab uses Go templates with `missingkey=error`, so a misspelled parameter fails loudly instead of yielding an empty string:

```yaml
goTemplate: true
goTemplateOptions: ["missingkey=error"]
template:
  metadata:
    name: 'argocd-demo-{{ .environment }}'          # environment=dev  -> argocd-demo-dev
  spec:
    source:
      path: 'argocd/apps/argocd-demo/overlays/isolated/{{ .environment }}'
    destination:
      namespace: 'argocd-demo-{{ .environment }}'
```

```text
environment = dev   ->  Application argocd-demo-dev   ->  overlays/isolated/dev   ->  namespace argocd-demo-dev
environment = prod  ->  Application argocd-demo-prod  ->  overlays/isolated/prod  ->  namespace argocd-demo-prod
```

The names are predictable because they are computed from the parameter, not random. **(demonstrated)** The generated spec was verified against the template: only `{{ .environment }}` was substituted; everything else is copied as is. The template has **no `syncPolicy`**, so every generated Application is manual-sync.

## 6. What was built

```text
argocd/
├── install/kustomization.yaml            # ApplicationSet controller un-scaled (Phase 3 patch removed)
├── applications/
│   ├── argocd-demo.yaml                  # existing, hand-made Application (unchanged)
│   ├── applicationsets.yaml              # NEW: Application that deploys the ApplicationSet from Git
│   ├── k8s-learning.yaml, traefik-config.yaml
├── applicationsets/
│   └── argocd-demo.yaml                  # NEW: the ApplicationSet (list generator: dev, prod)
└── apps/argocd-demo/
    ├── base/                             # existing
    └── overlays/
        ├── dev/, prod/                   # existing, unchanged
        └── isolated/{dev,prod}/          # NEW: thin overlays of dev/prod that only change the namespace
```

**The ownership design, and why.** The Application `argocd-demo` already owns the live resources in namespace `argocd-demo`, and two Applications must never claim the same resources. The overlays `dev` and `prod` both create a Namespace `argocd-demo` and objects inside it, so pointing generated Applications at them would collide with the existing one. Options considered:

| Option | Verdict |
|---|---|
| Generate Applications on the existing overlays and never sync one | Still two Applications rendering the same identities (shared-resource warnings, confusion). Rejected. |
| Retire the hand-made `argocd-demo` in favour of generated Applications | Needs deleting an Application (forbidden for this phase) and a controlled ownership hand-over. Deferred, see below. |
| Edit the existing overlays to change their namespace | Would move the *live* `argocd-demo` resources, which means recreation. Rejected. |
| Application-level Kustomize patches injected by the ApplicationSet template | Works, but puts manifest concerns inside the template and muddies the layering. Rejected. |
| **Thin `isolated` overlays: Kustomize overlays of the existing overlays, one namespace per environment** | **Chosen.** Nothing existing changes; every generated Application gets its own resource identities; the layering stays clean (Kustomize decides *what*, the ApplicationSet decides *how many and with what parameters*). |

`overlays/isolated/dev` is `resources: [../../dev]` plus `namespace: argocd-demo-dev` and a patch that renames the Namespace object (the namespace transformer does not rename the Namespace itself). Rendering was checked before use: four resources, the namespace on everything including the generated ConfigMap, the hashed ConfigMap reference still resolving, and **no identity shared with the live `argocd-demo` app**. **(demonstrated)**

**Result:** three independent environments run side by side. `argocd-demo` (dev overlay, namespace `argocd-demo`), `argocd-demo-dev` (namespace `argocd-demo-dev`) and `argocd-demo-prod` (namespace `argocd-demo-prod`). The Deployment and Service are named `argocd-demo` in all three, but in different namespaces, so they are different objects. Argo CD's own model confirmed it: with all Applications present, **23 resource identities were claimed and none by more than one Application**, with no shared-resource conditions.

**How the hand-made app could later be handed to the ApplicationSet (documented only, needs approval).** The end state "only ApplicationSet-generated Applications" requires retiring `argocd-demo`. The safe way: point a generated element at the *same* namespace and overlay, delete the hand-made Application **without cascade** (it has no finalizer, so the workloads stay), then sync the generated Application, which takes over the objects by rewriting their tracking annotation. That deletes an Application, so it was not done.

## 7. Ownership

```text
Application "applicationsets"          owns  ->  the ApplicationSet OBJECT   (tracking annotation, from Git)
ApplicationSet                         owns  ->  the generated Applications  (ownerReference, controller=true)
generated Application                  owns  ->  the Kubernetes resources    (tracking annotation, via Kustomize)
Kubernetes controllers                 own   ->  ReplicaSets, Pods, EndpointSlices
```

Evidence, read from the API and not from the UI **(demonstrated)**:

* The generated Application has `ownerReferences: [{kind: ApplicationSet, name: argocd-demo-environments, controller: true, blockOwnerDeletion: true}]`, and the UID matches the ApplicationSet's. The hand-made Applications have no owner.
* The ApplicationSet controller emitted `created Application "argocd-demo-dev"` / `"argocd-demo-prod"`, and the ApplicationSet status reported `ParametersGenerated=True`, `ResourcesUpToDate=True`, `ErrorOccurred=False`, health `Healthy`, and the generated Applications as its resources.
* The ApplicationSet object carries the tracking annotation of the `applicationsets` Application, while the generated Applications do **not**. They are **derived** objects: they exist because a controller created them, not because a file in Git declares them. This is the nuance: *the ApplicationSet is a resource deployed from Git; the Applications it generates are not files in Git.*
* The generated Applications have **no finalizer**, so deleting one would not cascade to the workloads.

**What if a generated Application is edited by hand?** Tested on `argocd-demo-dev` with a reversible change: `targetRevision` set to `HEAD`, an extra `revisionHistoryLimit`, plus an annotation and a label. Within about **one second** the ApplicationSet controller restored the template's `targetRevision`, **removed the extra field, and also removed the added annotation and label**. The workloads were untouched. Two lessons: the ApplicationSet owns the *whole* generated Application (spec and metadata), and it reacts quickly because it watches the Applications it owns. To change a generated Application, change the template (or the generator data) in Git. (Controlled exceptions exist, such as `ignoreApplicationDifferences`; **documented only**.) A hand-made Application, by contrast, is never overwritten by anything.

## 8. Enabling the ApplicationSet controller

The CRD, RBAC, Service and NetworkPolicy were already installed; only the controller Deployment was scaled to 0 by a Phase 3 patch. Enabling it was a controlled change, declared in Git:

1. **Inspect:** `replicas: 0`, image `argocd:v3.5.3`, no controller flags or `applicationsetcontroller.*` settings (defaults), no resource requests.
2. **Change:** remove the one patch in `argocd/install/kustomization.yaml`. Dex and the notifications controller stay scaled to 0.
3. **Preview:** `kubectl diff --server-side -k argocd/install` across the **whole** install showed exactly one difference: `replicas: 0 -> 1` on the ApplicationSet controller.
4. **Apply:** `kubectl apply -k argocd/install --server-side --force-conflicts`.
5. **Validate:** Ready in about 4 s (image cached), 0 restarts, **0 warnings or errors** in its log. It watches `Application`, `ApplicationSet` and `Secret` objects with 10 workers, and starts a webhook server on `:7000` that is unused here (no webhook infrastructure).

Resource impact **(measured)**: the controller uses about **26 MiB**. Node memory did not move: used 5.5 GiB and available 2.0 GiB before and after, swap about 1.0 GiB. Argo CD's five Pods total about 315 MiB. The four demo Pods each use under 1 MiB (`kubectl top` shows 0 Mi).

## 9. The lifecycle: separate loops with measured timings

These are different loops in different controllers. They are **not** one reconciliation loop.

The controlled cycle (add the `prod` element in Git) **(demonstrated)**:

```text
Git change (push)
   |  Argo CD polls Git  (its own loop)
   v  stage 1: application-controller detects the new revision  -> "applicationsets" Application OutOfSync    286 s after the push
manual sync of "applicationsets"
   |  Argo CD applies the ApplicationSet object
   v  stage 2: ApplicationSet spec updated in the cluster (prod element present)                              1.7 s after the sync request
   |  ApplicationSet controller reconciles  (a DIFFERENT controller)
   v  stage 3: Application argocd-demo-prod generated (OutOfSync/Missing, nothing deployed)                    0.25 s after stage 2
manual sync of "argocd-demo-prod"
   |  Argo CD renders the overlay with Kustomize, compares, applies
   v  stage 4: Namespace, ConfigMap, Service, Deployment created; 3/3 Pods Ready and Healthy                  about 11 s
Kubernetes (Deployment controller -> ReplicaSet -> Pods)
```

| Loop / event | Owner | Measured |
|---|---|---|
| Detect a new Git commit | Argo CD application-controller (polling; the 3-minute default plus jitter) | 286 s (a refresh would take seconds) |
| Apply the ApplicationSet definition | Argo CD, on a manual sync | 1.7 s |
| Generate the Application | **ApplicationSet controller** | 0.25 s (first creation: 0.3 s) |
| Manual edit of a generated Application reverted | ApplicationSet controller (watch on owned objects) | about 1 s |
| Deploy the workloads | Argo CD, on a manual sync | about 11 s for each environment |

Distinguish: *Git change detection* (Argo CD polling) is unrelated to *ApplicationSet reconciliation* (a separate controller reacting to a changed ApplicationSet object or an edited owned Application), which is unrelated to *Application sync* (Argo CD applying rendered manifests). Generating an Application deploys nothing; it only creates an object that then reports `OutOfSync/Missing`.

The list generator's parameters live in the ApplicationSet itself, so they change only when that object changes. Generators that read Git or clusters re-evaluate on a timer (**documented only**).

## 10. Manual sync stays the default

Two different "sync policies" exist and must not be confused:

| Where | What it controls | Set here |
|---|---|---|
| `Application.spec.syncPolicy` (also in the template) | Whether Argo CD **syncs** that Application automatically (`automated`, `selfHeal`, `prune`) | **absent**, so manual. All six Applications have an empty policy. |
| `ApplicationSet.spec.syncPolicy` | What the **ApplicationSet controller** may do to the Applications it generates | `applicationsSync: create-update` and `preserveResourcesOnDeletion: true` |

`applicationsSync: create-update` means the controller may create and update generated Applications but never delete them, for example if an element disappears from the list. The default would allow deletion. This matters more as the number of Applications grows. `preserveResourcesOnDeletion: true` documents that if the ApplicationSet itself is deleted, the workloads stay. **These two behaviours were configured but not exercised**: removing an element or deleting the ApplicationSet was deliberately not tested, because a mistake could remove Applications. **(documented only)**

Generation and synchronization stay separate on purpose: an ApplicationSet can create many Applications at once, and each one still needs an explicit, per-Application sync.

## 11. GitOps flow

```text
Git  (ApplicationSet + Kustomize overlays)
 |
 v   Argo CD "applicationsets"  -> deploys the ApplicationSet object
ApplicationSet controller  -> generates Applications from template + generator
 |
 v   each generated Application
Argo CD: render Kustomize overlay -> diff desired vs live -> manual sync
 |
 v
Kubernetes: Deployment/Service/ConfigMap/Namespace, then ReplicaSets and Pods by controllers
```

## 12. Production translation

The same pattern, with different generators and scale:

| In this lab | In a platform setting |
|---|---|
| List generator, 2 hand-written elements | **Git directory/file generator**: a new folder or file in Git becomes a new Application; **cluster generator**: one Application per registered cluster |
| `dev` and `prod` in one cluster, isolated by namespace | `dev`, `uat`, `stg`, `prod`, usually in **separate clusters** or projects, one Application per environment per service |
| One service | **100 services**: one ApplicationSet, or a **matrix** of services x environments (or business unit x environment x application), instead of hundreds of files |
| Manual sync | Automated sync with self-heal for lower environments, staged or **progressive syncs** and approvals toward production |
| Hand-bootstrapped root Application | An "app of apps" or a platform bootstrap that installs the ApplicationSets; **AppProjects** per team restricting repos, clusters and namespaces |
| `applicationsSync: create-update` | The same guardrail, plus pull-request generators for preview environments and policy for deletions |
| Polling | Webhooks so Git changes are noticed in seconds |

**Platform engineering and Backstage (concept only, not implemented).** A developer portal such as Backstage can *create or modify Git desired state*, for example by committing a new service definition or a new element/folder. The ApplicationSet then turns that Git change into the right Applications in the right clusters, and Argo CD deploys them. The portal owns the *workflow and intent*, Git owns the *record*, the ApplicationSet provides the *scalable Application orchestration layer*, and Argo CD and Kubernetes do the *reconciliation*.

## 13. Limitations of this single-node lab

* One cluster and one node, so environments are separated only by **namespace**; real environments would use separate clusters.
* The `isolated` overlays duplicate the demo workload (`argocd-demo` also runs from the original Application); this exists only to avoid ownership collisions.
* Phase 6 used only the **list generator**. Phase 7 added the **Git directory generator** (section 16); the Git file generator, matrix/merge, progressive syncs and `ignoreApplicationDifferences` are still documented only.
* Deletion behaviour (`applicationsSync`, removing an element) was configured but not exercised for the Phase 6 ApplicationSet; Phase 7 exercised it on the separate Git-generator ApplicationSet (section 16.4). Deleting an *ApplicationSet* (and `preserveResourcesOnDeletion`) is still untested.
* Sync is manual, so Git changes wait for a human, and detection is polling (about 4-5 minutes) with no webhook, because GitHub cannot reach a local WSL instance.
* The root `applicationsets` Application and the other Applications are still bootstrapped by hand; the ApplicationSet is deployed from Git, but the Application that deploys it is not.
* Public repository, no credentials, single `default` AppProject.

## 14. Lessons and common mistakes

1. **An ApplicationSet does not deploy anything itself.** It creates Applications; syncing them is a separate act (and a separate policy).
2. **Two Applications must never claim the same resources.** Give every generated Application its own resource identities (here, a namespace each) and verify with the claims across all Applications.
3. **Do not edit generated Applications.** The ApplicationSet owns their entire spec and metadata and reverts edits within about a second; change the template or generator instead.
4. **Ownership has layers**: tracking annotations connect Applications to resources; owner references connect an ApplicationSet to its Applications.
5. **Decide the deletion policy up front.** `applicationsSync: create-update` prevents an edit to a list from silently deleting an Application.
6. **Templates fail loudly with `missingkey=error`.** A typo in a parameter name should be an error, not an empty path.
7. **Keep Kustomize concerns in Kustomize.** The overlays decide what is deployed; the template only decides how many Applications and where.
8. **Measure before enabling controllers on a small VM.** The ApplicationSet controller cost about 26 MiB here; verify rather than assume.

## 15. Verify, troubleshoot, remove

```bash
kubectl get applicationsets -n argocd
kubectl get applications -n argocd -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,OWNER:.metadata.ownerReferences[0].name
kubectl describe applicationset argocd-demo-environments -n argocd
kubectl get application argocd-demo-dev -n argocd -o jsonpath='{.metadata.ownerReferences}'
kubectl logs -n argocd deploy/argocd-applicationset-controller
```

| Symptom | Check |
|---|---|
| No Application generated | Is the controller Running (`kubectl get pods -n argocd`)? `kubectl describe applicationset` conditions (`ErrorOccurred`, `ParametersGenerated`); its log |
| Template error | A misspelled parameter with `missingkey=error` shows in the ApplicationSet conditions |
| A generated Application keeps changing back | It is owned by the ApplicationSet; change the template in Git |
| `OutOfSync` on the ApplicationSet after a Git change | Expected until the manual sync of the `applicationsets` Application |
| Two Applications show shared-resource conditions | They render the same identities; give each its own namespace or names |

Remove (destructive; not done): deleting the ApplicationSet deletes its generated Applications (their workloads stay, because there are no finalizers and `preserveResourcesOnDeletion` is set); the namespaces `argocd-demo-dev` and `argocd-demo-prod` and their workloads would then be removed by hand. Disable the controller again by re-adding the scale-to-0 patch to `argocd/install/kustomization.yaml` and re-applying.

**Remaining topics (future phases):** the Git file generator, matrix and merge generators, multi-cluster ApplicationSets, progressive syncs, `ignoreApplicationDifferences`, retiring the hand-made `argocd-demo`, the orphaned-ConfigMap policy for the existing Applications, and Backstage-driven Git changes. (Automated sync, self-heal and prune were demonstrated on one sandbox in Phase 7; their policy for the other Applications is deliberately still manual, see [ADR-006](../decisions/ADR-006-gitops-automation-policy.md).)

---

## 16. The Git directory generator (Phase 7)

Phase 6 used a **list** generator: a human edits a list inside the ApplicationSet. The **Git directory generator** instead *discovers* the Application list from the repository layout, so adding a folder adds an Application. **(demonstrated)** on 2026-09-20.

```text
Git: argocd/apps/phase7-git-generator/<service>/overlays/<env>/
        |
        v   ApplicationSet "phase7-git-generator"   (git generator, directories glob)
        |   generates (owner reference), one per matching directory
        v
Application p7g-<service>-<env>      (manual sync; source path = the discovered directory)
        |   Argo CD's repo-server renders it with
        v
Kustomize  (overlays/<env> on ../../base)
        |   Argo CD compares and, on a manual Sync, applies
        v
Kubernetes  (Deployment -> ReplicaSet -> Pod)
```

### 16.1 What was built

| File | Role |
|---|---|
| [`argocd/applicationsets/phase7-git-generator.yaml`](../../argocd/applicationsets/phase7-git-generator.yaml) | The ApplicationSet; deployed from Git by the `applicationsets` Application (manual sync) |
| `argocd/apps/phase7-git-generator/service-a/{base,overlays/dev}/` | First application directory (Kustomize base + dev overlay, own namespace `p7g-service-a-dev`) |
| `argocd/apps/phase7-git-generator/service-b/{base,overlays/dev}/` | Second application directory, added later through Git only |

Generator configuration (the whole "discovery" rule):

```yaml
generators:
  - git:
      repoURL: https://github.com/Nanthagopal87/local-k8s-gitops-lab.git
      revision: main
      directories:
        - path: argocd/apps/phase7-git-generator/*/overlays/*
```

For `argocd/apps/phase7-git-generator/service-a/overlays/dev` the template sees `.path.path` (full path), `.path.basename` (`dev`) and `.path.segments` (the path as a list; index 3 is `service-a`). The Application name is `p7g-{{ index .path.segments 3 }}-{{ .path.basename }}`. The template contains **no manifest logic**: only names, the source path and the destination. Everything else (replicas, labels, ConfigMap values) stays in Kustomize. `goTemplate: true` with `missingkey=error` is kept. `applicationsSync: create-update` and `preserveResourcesOnDeletion: true` are kept as in ADR-005, and the template has no `syncPolicy`, so **every generated Application is manual sync**.

**Naming contract:** the overlay's `namespace:` and the template's `destination.namespace` must both be `p7g-<service>-<environment>`. Nothing enforces it; a mismatch would deploy into a namespace other than the one the Application shows.

### 16.2 Who answers which question

| Layer | Question it answers | In this experiment |
|---|---|---|
| **ApplicationSet** | Which Applications should exist? | Discovered two directories and generated two Applications; did not deploy anything |
| **Application** | Which Git source should this app reconcile? | `p7g-service-a-dev` points at `.../service-a/overlays/dev` on `main` |
| **Kustomize** | Which manifests does that path produce? | Base + dev overlay: Namespace, Deployment, Service, hash-named ConfigMap |
| **Argo CD** | Does the cluster match the rendered manifests? | `OutOfSync/Missing` before the manual sync, `Synced/Healthy` after |
| **Kubernetes controllers** | How should the runtime converge? | Deployment to ReplicaSet to Pod, unchanged by any of the above |

### 16.3 Experiments 1 and 2: discovery and adding an application

**Experiment 1: one qualifying directory.** `service-a` was committed *first* on its own (commit `1ff3163`); with no ApplicationSet yet, nothing reacted (still 7 Applications). Then the ApplicationSet was committed (commit `9216d34`; the timings below start from its push). A refresh of the `applicationsets` Application was requested to avoid waiting on polling for that one hop.

| Step | Time | Elapsed |
|---|---|---|
| Push of the ApplicationSet commit | 19:28:06.3 | |
| Refresh requested / `applicationsets` `OutOfSync` (Git detection, refresh-assisted) | 19:28:06.7 / 19:28:09.8 | 3.1 s |
| Manual Sync of `applicationsets` requested | 19:28:10.2 | |
| ApplicationSet object exists (Argo CD applied it) | 19:28:11.3 | 1.1 s after Sync |
| **Application `p7g-service-a-dev` generated** (ApplicationSet reconciliation) | 19:28:11.6 | **0.4 s** after the ApplicationSet existed |
| Manual Sync of the generated Application requested | 19:28:32.2 | |
| Sync operation `Succeeded` | 19:28:34.6 | 2.4 s |
| Workload `Healthy` (Pod ready) | 19:28:41.4 | 9.2 s after Sync |

Checks: the generated Application has `ownerReferences: ApplicationSet/phase7-git-generator (controller=true)`, path `argocd/apps/phase7-git-generator/service-a/overlays/dev`, no `syncPolicy`, and no finalizer. Before the manual sync it was `OutOfSync/Missing` and **the namespace had no resources**: generating an Application does not deploy its workload. Every live object carries `argocd.argoproj.io/tracking-id: p7g-service-a-dev:...`.

**Experiment 2: add a second application by directory only.** `service-b` was created by copying `service-a` with the names changed, committed and pushed at 19:29:08.6 (commit `94d827a`). **No Application was created by hand and no refresh was requested.**

* The Application `p7g-service-b-dev` appeared at **19:34:11, 302 s after the push** (natural polling), owned by `phase7-git-generator`, `OutOfSync/Missing`, with no workload until it was synced by hand.
* The controller log for that moment: `generated 2 applications`, `created Application`, `requeueAfter` 180 s.

**Why 271-344 s and not 180 s (partly inferred).** The ApplicationSet controller re-runs the generator on a 180 s timer (`requeueAfter`, seen in its log). One reconcile at 19:37:11 (91 s after the removal push in 3a) still returned the *old* list, so a second cache or timer in the repo-server also sits between Git and the generator. The stacked-timer explanation fits all three natural samples (302, 271 and 344 s), but I verified only the 180 s timer directly. A webhook would remove both delays.

### 16.4 Experiment 3: removing an application

**Inspection first** (before removing anything): the ApplicationSet's `applicationsSync` was `create-update`; neither the template nor either generated Application had a finalizer; both were manual sync; the resources of `service-b` were tracked only by `p7g-service-b-dev`; no Phase 4-6 Application referenced its namespace. With no finalizer, deleting the Application cannot cascade to its workload. So the experiment was safe to run in isolation. `service-b` was first synced by hand so a real workload existed.

| Step | What was done | Observed |
|---|---|---|
| **3a** removal, policy `create-update` | `git rm -r service-b`; push at 19:35:40 (commit `9c30147`) | Generator reflected the removal at 19:40:11 (`generated 1 applications`, 271 s). The Application was **not deleted**; it went `Sync: Unknown` with `ComparisonError: app path does not exist`. The Deployment, Service and ConfigMap kept their UIDs and the Pod kept running |
| **3b** policy `sync`, **temporary** | Set `applicationsSync: sync` in Git (commit `a0cc433`), manual sync of `applicationsets` | The controller **deleted the Application** about 4 s later (19:42:20). The workload was **untouched** (same UIDs, Pod running): it was now orphaned, its tracking-id naming an Application that no longer existed |
| **3c** restore | Reverted 3b (commit `8eeba03`), then re-added the directory with `git revert` of 3a (commit `b5b5224`) | The policy was `create-update` again. The Application was regenerated at 19:49:01 (344 s) and was **already `Synced/Healthy` with no sync operation at all**: identical UIDs prove the workload was **re-adopted, not recreated** |

Lessons: (1) `create-update` protects against a directory removal deleting an Application; (2) with deletion allowed, removing the directory removes the Application but, with no finalizer, **not** the workload; (3) adding `resources-finalizer.argocd.argoproj.io` to the template would make Application deletion also delete the workload (**documented only, not tested**); (4) an Application with the same name adopts resources whose tracking-id names it, so an accidental deletion is recoverable by reverting Git. Nothing was deleted from the cluster by hand in Phase 7's Git-generator work, and no Phase 4-6 Application was touched.

### 16.5 Ownership evidence

```text
ApplicationSet  phase7-git-generator                       (deployed from Git by Application "applicationsets")
   | ownerReference (kind ApplicationSet, controller=true)
   v
Application     p7g-service-a-dev                          (labels: service=service-a, environment=dev)
   | tracking annotation on every managed object
   v
Deployment      service-a   argocd.argoproj.io/tracking-id: p7g-service-a-dev:apps/Deployment:p7g-service-a-dev/service-a
Service         service-a   ...tracking-id: p7g-service-a-dev:/Service:p7g-service-a-dev/service-a
ConfigMap       service-a-config-<hash>   ...tracking-id: p7g-service-a-dev:/ConfigMap:p7g-service-a-dev/service-a-config-<hash>
```

No resource is claimed twice: every Application other than the owner lists zero resources in the `p7g-*` namespaces (checked across all Applications).

### 16.6 Application lifecycle under an ApplicationSet

| Event | Result (demonstrated unless noted) |
|---|---|
| Matching directory appears in Git | Application created, `OutOfSync/Missing`, nothing deployed |
| Application synced by hand | Workload deployed and `Healthy` |
| Directory content changes | The Application notices on its own refresh, like any Application |
| Directory removed, `create-update` | Application stays with `ComparisonError`; workload untouched |
| Directory removed, `sync` (deletion allowed) | Application deleted; workload stays (no finalizer) |
| Directory re-added | Application recreated with the same name; running workload re-adopted |
| Generated Application edited by hand | Reverted by the controller within about a second (Phase 6) |
| ApplicationSet deleted | **not tested** for this ApplicationSet (`preserveResourcesOnDeletion: true` is configured) |

### 16.7 Risks and safeguards

| Risk | Safeguard here |
|---|---|
| A glob that matches too much (or a directory renamed by accident) creates or drops Applications | Narrow glob (`*/overlays/*` under one family directory); `create-update` so nothing is deleted on its own; review Git changes to the family directory |
| Generator or Git errors produce an empty list, which with deletion allowed could delete Applications | Deletion is not allowed (`create-update`); the temporary `sync` window lasted a few minutes and was reverted |
| Name and namespace drift between the overlay and the template | Documented naming contract (section 16.1); not enforced |
| Deleting an Application that has a finalizer would delete its workload | No finalizer on the template (verified before the removal experiment) |

### 16.8 What was not tested, and what was not changed

* **Not tested:** the Git *file* generator, matrix and merge generators, `exclude` patterns, `requeueAfterSeconds` tuning, ApplicationSet deletion, finalizer cascade, generator behaviour when Git is unreachable, and more than two directories.
* **Not changed:** the Phase 6 ApplicationSet `argocd-demo-environments` and its Applications, the hand-made `argocd-demo`, `k8s-learning`, `traefik-config`, nginx, Traefik, the PVC, and every existing Application's sync policy.

### 16.9 Future migration considerations

* **Converting `argocd-demo-environments` to a Git generator** is possible (its `overlays/isolated/<env>` directories already have the right shape), but it changes how the existing generated Applications are produced: the list generator's Applications would need to be adopted by name to avoid recreation, and this was deliberately not done. Retiring the hand-made `argocd-demo` remains a separate, undecided step.
* **A Git file generator** (a small `config.json` per directory) would remove the naming contract by carrying the namespace and environment as data instead of deriving them from the path.
* **A matrix generator** (Git directories x clusters or environments) is the natural next step toward a fleet, and is where a `Backstage`-style scaffolder that commits a directory becomes useful.
* **Deletion policy for a real platform** is a decision for later; the lab keeps `create-update`.

### 16.10 Verify, troubleshoot, remove

```bash
kubectl get applicationsets -n argocd
kubectl get applications.argoproj.io -n argocd -o custom-columns=NAME:.metadata.name,OWNER:.metadata.ownerReferences[0].name,SYNC:.status.sync.status,HEALTH:.status.health.status
kubectl describe applicationset phase7-git-generator -n argocd
kubectl logs -n argocd deploy/argocd-applicationset-controller | grep phase7-git-generator
kubectl kustomize argocd/apps/phase7-git-generator/service-a/overlays/dev        # what the Application will render
```

| Symptom | Check |
|---|---|
| A new directory produced no Application | Wait up to about 6 minutes (two timers); does the path match `*/overlays/*` and is it on `main`? ApplicationSet conditions and log |
| Application `Sync: Unknown` with `app path does not exist` | Its directory was removed (or renamed) in Git; see section 16.4 |
| Application deployed into an unexpected namespace | The overlay `namespace:` and the template `destination.namespace` disagree (naming contract) |

Remove (destructive; not done): revert the Git commits that added the ApplicationSet and the `phase7-git-generator/` directory, sync the `applicationsets` Application, delete the generated Applications, and delete the `p7g-*` namespaces by hand (their Namespace objects carry `Prune=false,Delete=false`, so Argo CD never removes them).
