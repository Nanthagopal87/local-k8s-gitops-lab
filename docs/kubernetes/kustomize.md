# Kustomize Fundamentals and Environment Overlays (Phase 5)

Kustomize as a declarative way to compose Kubernetes YAML, and how Argo CD consumes it. The learning application is `argocd-demo`. Everything marked **(demonstrated)** was run on this cluster on 2026-09-20; anything marked **(documented only)** was not exercised. Decisions are in [ADR-004](../decisions/ADR-004-kustomize-base-overlays.md).

## Contents

1. [What Kustomize is](#1-what-kustomize-is)
2. [Why it is useful](#2-why-it-is-useful)
3. [Base](#3-base)
4. [Overlay](#4-overlay)
5. [kustomization.yaml](#5-kustomizationyaml)
6. [Patches](#6-patches)
7. [Metadata customization](#7-metadata-customization)
8. [ConfigMap generation](#8-configmap-generation)
9. [kustomize build](#9-kustomize-build)
10. [Argo CD + Kustomize](#10-argo-cd--kustomize)
11. [Environment switching](#11-environment-switching)
12. [Kustomize vs Helm](#12-kustomize-vs-helm)
13. [Common mistakes](#13-common-mistakes)
14. [GitOps implications](#14-gitops-implications)
15. [Lessons learned](#15-lessons-learned)

---

## 1. What Kustomize is

Kustomize takes plain Kubernetes manifests plus a small `kustomization.yaml` that says how to combine and modify them, and **prints the resulting manifests**. It has no templating language: the inputs are valid YAML, and so are the outputs.

```text
manifests + kustomization.yaml  --kustomize build-->  rendered manifests (YAML text)
```

It is **not a controller**. It never talks to the cluster and never reconciles anything. Something else has to apply its output: `kubectl apply`, or Argo CD. Kubernetes controllers then maintain the runtime state. In this lab:

```text
Kustomize   renders desired state from files
Argo CD     compares it with the cluster and (on a manual sync) applies it
Kubernetes  controllers keep the running objects at that state
Git         is where the desired state lives
```

The Kustomize used here is the one bundled in `kubectl` (`v1.36.4+k3s1`, Kustomize v5.8.1). Argo CD's repo-server ships its own, also v5.8.1. No standalone `kustomize` binary was installed or needed.

## 2. Why it is useful

* **One description, several variants.** The common parts live once; each environment states only its differences, instead of copying whole manifests.
* **Plain YAML in, plain YAML out**, so the files stay valid manifests and diffs stay readable.
* **Built into `kubectl` and Argo CD**, with nothing extra to run.
* **Reviewable in Git.** A difference between `dev` and `prod` is a few lines in one file.

## 3. Base

A base is a directory with a `kustomization.yaml` that lists ordinary manifests. It holds what is **common to every environment**. [`argocd/apps/argocd-demo/base/`](../../argocd/apps/argocd-demo/base/):

```text
base/
├── kustomization.yaml     # lists the resources + the generated ConfigMap + the namespace transformer
├── namespace.yaml         # Namespace argocd-demo
├── deployment.yaml        # Deployment argocd-demo (replicas 2, busybox:1.37, envFrom the generated ConfigMap)
└── service.yaml           # ClusterIP Service argocd-demo
```

The three manifests were `git mv`d from the pre-Kustomize layout, so history follows them and nothing is duplicated. **(demonstrated)**

## 4. Overlay

An overlay is a directory whose `kustomization.yaml` references a base (`resources: [../../base]`) and layers **only the environment's differences** on top.

```text
            base  (common configuration)
              |
      +-------+-------+
      v               v
    dev             prod          overlays/dev/kustomization.yaml, overlays/prod/kustomization.yaml
   (1 replica)   (3 replicas)
```

Each overlay is about 40 lines including comments. Neither contains a full manifest. The values are illustrative for a learning lab, **not real production sizing**. **(demonstrated)**

## 5. kustomization.yaml

The lab's dev overlay, trimmed:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources: [../../base]                 # what to build on
patches: [...]                          # modify fields of base objects
labels: [...]                           # add labels
images: [...]                           # change image names/tags
configMapGenerator: [...]               # create ConfigMaps from literals/files
```

The base additionally uses `namespace: argocd-demo` (a transformer that sets the namespace on every namespaced object) and its own `configMapGenerator`. The repo already contained one more Kustomization from Phase 3, [`argocd/install/`](../../argocd/install/kustomization.yaml), which uses a *remote* base (the pinned Argo CD manifest) plus patches; it is the same mechanism used in the opposite direction (documented, unchanged in this phase).

## 6. Patches

A patch modifies fields of an object that came from the base. **Both mechanisms were used, on purpose. (demonstrated)**

**Strategic merge** (dev): a partial object that is merged onto the base one. It reads like the YAML it changes.

```yaml
patches:
  - target: {kind: Deployment, name: argocd-demo}
    patch: |-
      apiVersion: apps/v1
      kind: Deployment
      metadata: {name: argocd-demo}
      spec:
        replicas: 1
```

**JSON 6902** (prod): an explicit list of operations addressed by path. Precise for one value; more mechanical.

```yaml
patches:
  - target: {kind: Deployment, name: argocd-demo}
    patch: |-
      - op: replace
        path: /spec/replicas
        value: 3
```

Why patches instead of copying manifests: one copy of everything else, and a reviewer sees only what differs. When a patch gets complicated (many operations, patching list items by index, patches that need to know other patches), it is a sign that the base is doing too much: split it into two bases, use a Kustomize component, or move the variability into real parameters. Keep overlays small.

## 7. Metadata customization

Three ways to change names and labels, with very different risk on a **live** app **(all demonstrated, two of them only in scratch renders)**:

| Mechanism | What it changes | Used here? | Why |
|---|---|---|---|
| `labels` with `includeSelectors: false` | Adds labels to each object's `metadata` only | **Yes** (`environment: dev` / `prod`) | Selectors and Pod templates are untouched, so nothing is replaced or rolled. |
| `commonLabels` (deprecated) | Adds labels to metadata **and rewrites selectors and Pod templates** | No | A Deployment `spec.selector` is immutable. A server-side dry run against the live app was rejected with `spec.selector … field is immutable`. Applying it for real would need a delete and recreate. Kustomize itself warns it is deprecated in favour of `labels`. |
| `namePrefix` / `nameSuffix` | Renames objects | No | In a scratch render it renamed the Deployment, Service and ConfigMap (the Namespace was left alone). To Kubernetes and Argo CD these are new objects, so the originals would be orphaned and new Pods created. |

Names and selectors of the existing `argocd-demo` objects were therefore left untouched in every overlay. If you want a *second* environment side by side, give it its own namespace or resource identities and its own Application (Phase 6 territory); do not rename the live one.

## 8. ConfigMap generation

`configMapGenerator` builds a ConfigMap from literals or files. Each overlay sets `ENVIRONMENT`:

```yaml
# base                                 # dev overlay                            # prod overlay
configMapGenerator:                    configMapGenerator:                      configMapGenerator:
  - name: argocd-demo-config             - name: argocd-demo-config               - name: argocd-demo-config
    literals: [ENVIRONMENT=base]           behavior: merge                          behavior: merge
                                           literals: [ENVIRONMENT=dev]              literals: [ENVIRONMENT=prod]
```

Behaviours **(demonstrated)**:

* **The name gets a content hash**: `argocd-demo-config-fmmmfb97gg` (dev), `…-5bcd24kd97` (prod). Kustomize rewrites the Deployment's `envFrom` reference to the hashed name. So changing a value changes the name, changes the Pod template, and **Kubernetes rolls the Pods**. A hand-edited ConfigMap would not restart anything (the Phase 2 lesson about env vars), and this is the Kustomize answer to it.
* **The reference is rewritten only if both objects are in the same namespace.** See the mistakes in [section 13](#13-common-mistakes).
* **Old hashed ConfigMaps are not removed by Kustomize.** With Argo CD, an unreferenced old one becomes an orphan (see [section 11](#11-environment-switching)).

**ConfigMap vs Secret.** A ConfigMap holds non-sensitive settings as plain text. A Secret is a separate object type with different access control and handling, but its data is only base64-encoded, not encrypted. Kustomize's `secretGenerator` exists, but it is **not a secrets-management solution**: its inputs still live in Git and it does not encrypt anything. No credential was put in any ConfigMap or overlay, and the fake `demo-web-secret` remains a manual exception outside Argo CD (see [gitops-adoption.md](../argocd/gitops-adoption.md#7-secret-boundary)).

## 9. kustomize build

```bash
kubectl kustomize argocd/apps/argocd-demo/base            # print the base
kubectl kustomize argocd/apps/argocd-demo/overlays/dev    # print the dev environment
kubectl kustomize argocd/apps/argocd-demo/overlays/prod   # print the prod environment
```

| Command | What it does |
|---|---|
| `kubectl kustomize <dir>` | **Renders and prints** the manifests. Never contacts the cluster. Verified: rendering left the live Deployment's `generation` unchanged. |
| `kubectl apply -f <file-or-dir>` | Sends the manifests **exactly as written** to the API server. It does not read `kustomization.yaml`. |
| `kubectl apply -k <dir>` | Renders with Kustomize **and** applies the result (`kubectl kustomize <dir> \| kubectl apply -f -`). |
| Argo CD | Renders the path it is given (it detects a `kustomization.yaml`) and reconciles the result; nothing is applied until a sync is requested. |

Useful before any sync: `kubectl kustomize <dir> | kubectl diff -f -` and `… | kubectl apply --dry-run=server -f -` (what would change, and would the API server accept it). **(demonstrated)** Rendering is deterministic, so the same ConfigMap hash came out of `kubectl` and out of Argo CD.

Checks run on every overlay before it was used: valid YAML; exactly four resources (Namespace, Deployment, Service, ConfigMap); the namespace set on all of them including the generated ConfigMap; the expected replicas, image, labels and ConfigMap data; hashed reference correct; selectors and names of the existing objects unchanged; and no immutable-field error from a server-side dry run.

## 10. Argo CD + Kustomize

* Argo CD **detects** Kustomize by finding a `kustomization.yaml` in the Application's `path`. The Application's `status.sourceType` changed from `Directory` to `Kustomize` after re-pointing it at `overlays/dev`, and Argo CD reported the image it found (`busybox:1.37`) from the rendered result. **(demonstrated)**
* The Application itself barely changed: only `spec.source.path` (now `argocd/apps/argocd-demo/overlays/dev`). No `kustomize:` block was needed.
* **Argo CD compares the rendered output, not the files.** New commits show up as `OutOfSync`, and the field-level diff is between the rendered desired state and the live objects.
* **Stage 1 was a pure refactor.** The base + an empty dev overlay rendered semantically identical to the three manifests already deployed (checked by parsing and comparing objects). After re-pointing the Application it showed **`Synced` with no sync at all**, and the Deployment's UID and `generation` did not change.
* Sync stayed **manual** with `prune: false` throughout. Automated sync, self-heal and prune are still off on all three Applications.

```text
Git (overlay + base)  ->  Kustomize renders  ->  Argo CD diff (rendered desired vs live)
        ->  OutOfSync  ->  manual sync  ->  Kubernetes API  ->  Kubernetes controllers
```

## 11. Environment switching

A single Application, `argocd-demo`, is switched between overlays by changing `spec.source.path` in `argocd/applications/argocd-demo.yaml` (committed to Git, then applied; the Application objects themselves are still bootstrapped by hand). Sequence used, as required: render both overlays, compare, identify the change, look at Argo CD's diff, sync manually, validate. **(demonstrated)**

What the dev to prod comparison showed (rendered): the `environment` label, `ENVIRONMENT` (dev to prod), the ConfigMap name (new hash), `replicas` 1 to 3, and the Deployment's `envFrom` reference. Nothing else. Names, selectors and the image were identical.

| Step | Observed |
|---|---|
| Stage 1: re-point at `dev` (no-op refactor) | `Synced` immediately, nothing applied |
| Stage 2: real dev differences, sync | about 16 s; Deployment updated in place (same UID; `generation` 5 to 6), Pods 2 to 1 by rolling update; new ConfigMap created; app says `environment=dev` |
| Switch to `prod`, Argo CD diff before syncing | new ConfigMap `would be CREATED`; old dev ConfigMap `would need PRUNING`; Deployment differs in label, replicas, container |
| Sync `prod` | about 32 s; 3/3 ready; same UIDs; `generation` 6 to 7; all 12 requests said `environment=prod` from 3 distinct Pods |
| Switch back to `dev`, sync | about 18 s; the existing dev ConfigMap was reused (`unchanged`); the earlier dev ReplicaSet was reused too |

**The one thing that did not go green, and why.** After each switch Argo CD reports the previous generated ConfigMap as `PruneSkipped (ignored (requires pruning))`, and the application stays **`OutOfSync`** on that single object, even though everything else is `Synced` and `Healthy`. This is not a bug: the old ConfigMap is still tracked by the Application but no longer in Git, and pruning is disabled by design. It is a direct consequence of two features working together (hash-named generated ConfigMaps and manual sync without prune). It was **not** cleaned up, because pruning is one of the three settings that need explicit approval. The options are listed in ADR-004.

Only one Application ever manages these resources. Two Applications (`argocd-demo-dev`, `argocd-demo-prod`) pointing at overlays that produce the same Deployment name in the same namespace would fight over it.

## 12. Kustomize vs Helm

Not used here (no Helm was introduced), but they solve different problems:

| | Kustomize | Helm |
|---|---|---|
| Idea | Overlay and patch **existing YAML** | **Package** an application as a chart and template it |
| Inputs | Plain manifests + `kustomization.yaml` | Templates + `values.yaml` |
| Templating language | None | Go templates |
| Configuration | Patches per environment | Values per environment (`--set`, values files) |
| Distribution | Directories/repos | Charts in repositories (versioned, dependencies) |
| Good for | Your own manifests with a few per-environment differences | Installing third-party software or parameterising a reusable app |

Argo CD can deploy either (and Helm charts rendered through Kustomize). The Traefik `HelmChartConfig` in this lab already touches Helm indirectly: k3s installs Traefik from a Helm chart, and we only supply values.

## 13. Common mistakes

Real ones found in this phase are marked **(hit here)**.

1. **A generated ConfigMap has no namespace, so the hashed-name reference silently is not rewritten. (hit here)** The Deployment was in namespace `argocd-demo` and the generated ConfigMap had none, so the `envFrom` kept the un-hashed name and would have pointed at a ConfigMap that does not exist. A `kubectl diff` preview (it showed `namespace: default`) and a validation script caught it. Fix: set `namespace:` in the kustomization so it applies to generated objects too, then verify the reference.
2. **`commonLabels` on an existing Deployment** rewrites the immutable selector. Use `labels` with `includeSelectors: false`. **(demonstrated)**
3. **`namePrefix` or `nameSuffix` on live objects** renames them, which is a replacement. **(demonstrated in scratch)**
4. **Two Applications, one resource.** Competing owners. Use one Application per resource identity.
5. **Assuming `kubectl apply -f overlays/dev` uses the overlay.** It applies the file `kustomization.yaml` as a (invalid) manifest. Use `-k` or `kubectl kustomize`.
6. **Putting secrets in a generator.** Generators do not encrypt; do not put credentials in overlays or ConfigMaps.
7. **Forgetting orphans with hashed ConfigMaps and no prune. (hit here)** See the end of section 11.
8. **Trusting the text of the output without validating it**: render, then check the specific things that matter (names, selectors, namespaces, references) and dry-run against the API server.
9. **Overgrown overlays**: if patches multiply, the base is wrong (section 6).
10. **Bases outside the repository path Argo CD clones**: a relative `../../base` must stay inside the repo; it does here.

## 14. GitOps implications

* Git holds **inputs to a render**, not the final manifests. A review of an overlay change should include the rendered diff (`kubectl kustomize` before and after).
* **Refactoring can be a no-op and should be proven so** (render both, compare, expect `Synced`).
* Environment is expressed as **which overlay the Application reads**, so a promotion is a Git commit (change `path`, or change the overlay), and a rollback is a `git revert`.
* **Config changes roll workloads** when the ConfigMap is generated with a hash, unlike a hand-edited ConfigMap.
* The hash trade-off: every distinct config leaves an **orphan** until it is pruned. Decide deliberately how orphans are cleaned (prune policy, targeted prune, or ignoring them).
* Ownership does not change: `argocd-demo` is still Argo CD's, and `k8s-learning`, `traefik-config` and the manual exceptions are untouched by this phase.

## 15. Lessons learned

1. Kustomize renders; it does not reconcile. Reconciliation is Argo CD's job and runtime state is Kubernetes'.
2. A Kustomize refactor can and should be a provable no-op before real changes are introduced.
3. Prefer `labels` (metadata only) over `commonLabels`, and never rename live objects with a prefix. Validate with a server-side dry run.
4. `kubectl kustomize` plus `kubectl diff` and `apply --dry-run=server` turn "hope it works" into evidence.
5. Name-reference rewriting is namespace-sensitive: generated ConfigMaps must be namespaced.
6. Hash-named ConfigMaps make config changes roll Pods, and leave orphans under manual sync, which makes the prune setting concrete.
7. One Application per resource identity; switching overlays is a change of `path`, verified before syncing.
8. Keep overlays small, and use them for differences only.
9. Argo CD's Kustomize and `kubectl`'s produced identical output here (same hash), which is what makes local rendering a trustworthy preview.

**Not exercised (documented only):** `nameSuffix`, `replacements`, Kustomize components, `secretGenerator`, remote bases beyond the existing `argocd/install`, and changing the image tag per environment (kept at the one cached tag `busybox:1.37` on purpose).
