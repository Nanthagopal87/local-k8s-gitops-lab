# Argo CD ApplicationSets (Phase 6)

What an ApplicationSet is, how it generates Applications, and how that sits on top of Kustomize and Argo CD, demonstrated on the lab's `argocd-demo` app. Everything marked **(demonstrated)** was run on this cluster on 2026-09-20; **(documented only)** was not exercised. Decisions are in [ADR-005](../decisions/ADR-005-applicationsets.md). Kustomize itself is covered in [kustomize.md](../kubernetes/kustomize.md).

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
* Only the **list generator** was used; other generators, matrix/merge, progressive syncs and `ignoreApplicationDifferences` are documented only.
* Deletion behaviour (`applicationsSync`, removing an element, deleting the ApplicationSet) was configured but not exercised.
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

**Remaining topics (future phases):** Git directory/file generator, matrix and merge generators, multi-cluster ApplicationSets, progressive syncs, `ignoreApplicationDifferences`, automated sync, self-heal and prune (still undecided), retiring the hand-made `argocd-demo`, and Backstage-driven Git changes.
