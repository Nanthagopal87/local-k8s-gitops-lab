# Local Kubernetes GitOps Lab

A lightweight local Kubernetes and GitOps laboratory running on an existing WSL2 Ubuntu environment.

The purpose of this project is to build a practical Kubernetes and Argo CD environment for learning, experimentation, and Platform Engineering practice without relying on Docker Desktop or other heavyweight desktop container platforms.

---

## 1. Objectives

This project focuses on:

* Kubernetes fundamentals
* k3s
* kubectl
* Argo CD
* GitOps
* Declarative Kubernetes configuration
* Kustomize
* Kubernetes application lifecycle
* Kubernetes networking and services
* RBAC and security fundamentals
* Repeatable infrastructure setup
* Platform Engineering concepts

The environment is intentionally lightweight and designed for local development and learning.

---

## 2. Target Architecture

The initial architecture is intentionally simple:

```text
Windows
   |
   +-- WSL2
        |
        +-- Ubuntu
             |
             +-- k3s
                  |
                  +-- Kubernetes
                  |
                  +-- Argo CD
                       |
                       +-- Git Repository
                            |
                            +-- Kubernetes Manifests
                            |
                            +-- Applications
```

### GitOps flow

```text
                 Git Repository
                       |
                       | Desired State
                       v
                    Argo CD
                       |
                       | Reconciliation
                       v
                   Kubernetes
                       |
                       v
                 Running Workload
```

Git is treated as the source of truth for application deployment configuration.

---

## 3. Technology Stack

### Initial stack

| Component | Purpose                             |
| --------- | ----------------------------------- |
| WSL2      | Linux environment on Windows        |
| Ubuntu    | Local Linux runtime                 |
| k3s       | Lightweight Kubernetes distribution |
| kubectl   | Kubernetes CLI                      |
| Argo CD   | GitOps continuous delivery          |
| Git       | Source control and desired state    |
| Kustomize | Kubernetes configuration management |

### Future possibilities

The project may later explore:

* Helm
* Argo CD ApplicationSets
* Multi-environment GitOps
* Progressive delivery
* Policy as Code
* Open Policy Agent
* Secrets management
* Observability
* Backstage
* Platform Engineering workflows

These will be introduced only when they provide a clear learning or architectural benefit.

---

## 4. Design Principles

The project follows these principles:

```text
Simple          > Complex
Declarative     > Imperative
Reproducible    > Manual
Documented      > Implicit
Observable      > Assumed
Least privilege > Excessive privilege
Small changes   > Large changes
```

The environment should remain easy to understand, recreate, troubleshoot, and remove.

---

## 5. Repository Structure

Target repository structure:

```text
local-k8s-gitops-lab/
│
├── CLAUDE.md
├── README.md
│
├── docs/
│   ├── architecture/
│   ├── setup/
│   ├── operations/
│   └── decisions/
│
├── scripts/
│
├── kubernetes/
│   ├── learning/          # Phase 2 fundamentals exercises (namespace k8s-learning)
│   ├── namespaces/
│   └── platform/          # cluster platform config (traefik/helmchartconfig.yaml)
│
├── argocd/
│   ├── install/
│   ├── projects/
│   ├── applications/
│   └── applicationsets/
│
└── apps/
    └── demo-app/
        ├── base/
        └── overlays/
            └── local/
```

The repository can evolve as the lab grows.

---

## 6. Project Phases

### Phase 0 — Environment Inspection

Inspect the existing WSL2 Ubuntu environment.

Validate:

* Ubuntu
* WSL2
* systemd
* CPU and memory
* disk
* kubectl
* k3s
* container runtime
* Git
* Helm
* existing Kubernetes configuration
* existing Argo CD

No system changes should be made during this phase.

---

### Phase 1 — k3s

Build a lightweight single-node Kubernetes cluster using k3s.

Objectives:

* Install k3s
* Configure kubectl
* Verify cluster health
* Understand k3s components
* Understand the local Kubernetes architecture

Basic validation:

```bash
kubectl get nodes
kubectl get pods -A
```

---

### Phase 2 — Kubernetes Fundamentals

Explore:

* Pods
* Deployments
* ReplicaSets
* Services
* ConfigMaps
* Secrets
* Namespaces
* Labels
* Selectors
* Resource requests and limits
* Probes
* Ingress
* RBAC

The goal is understanding rather than simply running commands.

---

### Phase 3 — Argo CD

Install Argo CD into the local Kubernetes cluster.

Objectives:

* Understand Argo CD architecture
* Install Argo CD
* Access the Argo CD interface
* Understand Applications
* Understand Projects
* Understand synchronization
* Understand health status

---

### Phase 4 — GitOps

Create a simple GitOps workflow:

```text
Developer
    |
    v
Git Repository
    |
    v
Argo CD
    |
    v
Kubernetes
```

Deploy a small demo application through Argo CD.

Demonstrate:

1. Initial synchronization
2. Application health
3. Git change
4. Argo CD detects the change
5. Kubernetes is reconciled
6. Application reaches the desired state

---

### Phase 5 — Kustomize

Introduce:

```text
apps/
└── demo-app/
    ├── base/
    └── overlays/
        └── local/
```

Understand the difference between:

* Base configuration
* Environment-specific overlays
* Argo CD
* Kubernetes desired state

---

### Phase 6 — Argo CD ApplicationSets

Explore ApplicationSets after the basic Argo CD workflow is stable.

Potential use cases:

* Multiple applications
* Multiple environments
* Environment-specific deployments
* Application generation

---

### Phase 7 — Multi-Environment GitOps

Experiment with:

```text
dev
uat
stg
prd
```

The local lab will not attempt to reproduce a production environment. The objective is to understand the GitOps patterns that can later be applied to larger environments.

---

### Phase 8 — Platform Engineering

Potential future architecture:

```text
                 Backstage
                     |
                     v
                Git Repository
                     |
                     v
                  Argo CD
                     |
                     v
                Kubernetes
```

Backstage may eventually be introduced as the platform front door.

This phase is intentionally deferred until Kubernetes, Argo CD, and GitOps fundamentals are stable.

---

## 7. Non-Goals

This project is not intended to:

* Build a production Kubernetes cluster
* Provide Kubernetes HA
* Reproduce a complete GKE environment locally
* Build a complete enterprise landing zone
* Replace a cloud platform
* Introduce unnecessary infrastructure components
* Optimize for maximum feature coverage

The priority is a clean, lightweight, understandable engineering laboratory.

---

## 8. Security

Although this is a local environment, reasonable security practices should be followed.

Do not commit:

* kubeconfig files
* passwords
* access tokens
* private keys
* real credentials
* sensitive Kubernetes Secrets

Administrative interfaces should preferably remain accessible only from the local machine.

---

## 9. Documentation

Important implementation decisions and troubleshooting solutions should be documented.

Documentation should explain:

* What was implemented
* Why it was implemented
* How it works
* How to verify it
* How to troubleshoot it
* How to remove it

Architecture decisions should be recorded as ADRs when appropriate.

---

## 10. Current Status

**Status:** Phase 2 complete; nginx/Traefik port conflict resolved (Phase 2.6)

| Phase | Status |
| ----- | ------ |
| Phase 0 — Environment Inspection | Complete |
| Phase 1 — k3s | Complete (single-node k3s `v1.36.4+k3s1`, see [docs/setup/k3s.md](docs/setup/k3s.md)) |
| Phase 2 — Kubernetes Fundamentals | Complete (see [docs/kubernetes/fundamentals.md](docs/kubernetes/fundamentals.md); manifests in [`kubernetes/learning/`](kubernetes/learning/)) |
| Phase 2.5 — Git repository | Initialized locally (branch `main`, initial commit created). **Remote: not configured**, nothing pushed. |
| Phase 2.6 — nginx/Traefik port conflict | Complete (Traefik moved to host ports 8880/8843, see [ADR-001](docs/decisions/ADR-001-traefik-alternate-host-ports.md)) |
| Phase 3 — Argo CD | Pending (needs a Git remote that the cluster can reach; not configured yet) |

Local entry points (this WSL2 machine):

| Service | Where | Notes |
| ------- | ----- | ----- |
| nginx + Backstage | `http(s)://localhost` on `80`/`443` | Host nginx, outside Kubernetes. Owns 80/443 again. |
| Traefik (k3s Ingress) | `http://localhost:8880`, `https://localhost:8843` | From WSL. Send the Ingress `Host` header (`curl --resolve`). |
| Traefik from Windows | `http://<WSL-IP>:8880` | Windows `localhost:8880` does not work; the WSL IP can change after a restart. |

The nginx/Traefik conflict found in Phase 1 is resolved; details in [docs/setup/k3s.md](docs/setup/k3s.md#7-nginx-on-ports-80443).

Current focus:

> Build a lightweight k3s Kubernetes cluster on existing WSL2 Ubuntu and deploy Argo CD using GitOps principles.
> The cluster and the Kubernetes fundamentals are done; installing Argo CD (Phase 3) is the next step.

---

## 11. Learning Philosophy

The objective is not simply to make Kubernetes work.

For important components, understand:

```text
What is it?
    ↓
Why do we need it?
    ↓
How does it work?
    ↓
How does Kubernetes use it?
    ↓
How does Argo CD interact with it?
    ↓
How would this translate to a production platform?
```

Automation should make the environment reproducible without hiding the underlying concepts.
