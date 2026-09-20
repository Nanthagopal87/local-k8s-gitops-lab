# Claude Code Project Instructions

## 1. Project Context

This repository is a lightweight local Kubernetes and GitOps laboratory.

The environment runs inside an existing WSL2 Ubuntu installation on a Windows machine.

The primary goal is to learn and practice:

* Kubernetes
* k3s
* kubectl
* Argo CD
* GitOps
* Kustomize
* Platform Engineering concepts

This is a learning and engineering laboratory, not a production environment.

---

# 2. Primary Objective

Build the environment incrementally.

Initial target:

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
                       +-- Git
                            |
                            +-- Applications
```

The immediate priority is:

> Kubernetes + Argo CD + GitOps

Do not introduce future platform components prematurely.

---

# 3. Runtime Requirements

The target runtime is:

* Windows host
* Existing WSL2
* Existing Ubuntu distribution
* k3s
* kubectl
* Argo CD
* Git
* Kustomize

The environment must remain lightweight.

---

# 4. Explicitly Prohibited Tools

Do NOT install or introduce the following unless the user explicitly requests them:

* Docker Desktop
* Rancher Desktop
* Podman
* kind
* k3d
* Minikube

Do not install a desktop container-management platform.

Do not introduce another container runtime unless technically necessary and explicitly approved.

The preferred Kubernetes runtime is:

> k3s directly inside WSL2 Ubuntu.

---

# 5. Resource Efficiency

This environment is intended to run on a developer laptop.

Prioritize:

* Low memory usage
* Low CPU usage
* Minimal background services
* Minimal dependencies
* Single-node Kubernetes
* Lightweight workloads

Avoid unnecessary components.

Do not install a complete observability stack, service mesh, database, or other heavyweight platform component unless it is explicitly required for the current learning objective.

Before installing a component, explain:

1. Why it is needed.
2. What it provides.
3. Its expected resource impact.
4. Whether there is a lighter alternative.

---

# 6. Environment Inspection

Before installing or modifying anything, inspect the current environment.

Check:

```text
Ubuntu version
WSL/kernel
systemd
CPU
Memory
Disk
kubectl
k3s
container runtimes
Git
Helm
Kubernetes configuration
Argo CD
```

Do not assume the environment is clean.

Do not overwrite an existing installation.

Do not reset an existing Kubernetes environment.

Do not remove an existing configuration without explicit approval.

---

# 7. Change Management

For system-level changes:

1. Inspect the current state.
2. Explain the proposed change.
3. Identify potential side effects.
4. Make the smallest required change.
5. Validate the result.
6. Update documentation.

Do not blindly execute installation commands.

Do not use destructive commands as a first troubleshooting step.

Never delete or reset the Kubernetes cluster without explicit user approval.

---

# 8. k3s

Use k3s as the local Kubernetes distribution.

Initial cluster requirements:

* Single node
* Local development
* Lightweight
* Reproducible
* Easy to troubleshoot

Do not configure HA or multiple nodes unless explicitly requested.

Use the default k3s architecture initially unless there is a documented reason to change it.

---

# 9. kubectl

Configure kubectl for normal user operation where practical.

Avoid unnecessary use of:

```bash
sudo kubectl
```

Ensure kubeconfig permissions are appropriate.

Never commit kubeconfig files to Git.

Never expose kubeconfig credentials in repository files.

---

# 10. Kubernetes Practices

Prefer declarative Kubernetes configuration.

Persistent resources should be represented as version-controlled manifests.

Prefer:

* Namespaces
* Labels
* Selectors
* Deployments
* Services
* ConfigMaps
* Secrets
* Resource requests
* Resource limits
* Readiness probes
* Liveness probes where appropriate
* Least-privilege RBAC

Avoid using imperative kubectl commands for persistent configuration.

Temporary imperative commands are acceptable for:

* Inspection
* Troubleshooting
* One-time diagnostics

---

# 11. GitOps

Git is the source of truth for application deployment configuration.

Target flow:

```text
Git
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
Running Application
```

Persistent application configuration should not be manually changed through kubectl.

If a manual change is required for troubleshooting:

1. Clearly identify it.
2. Explain why it is temporary.
3. Restore the Git-managed state afterward.
4. Document reusable lessons.

---

# 12. Argo CD

Argo CD is the GitOps controller.

Initial objectives:

1. Install Argo CD.
2. Verify Argo CD components.
3. Configure local access.
4. Understand Argo CD architecture.
5. Create an Application.
6. Connect it to Git.
7. Deploy a demo application.
8. Verify synchronization.
9. Demonstrate drift detection.
10. Demonstrate reconciliation.

Do not introduce advanced features until the basic workflow is working.

Future topics:

* AppProjects
* ApplicationSets
* Automated sync
* Self-healing
* Pruning
* Helm
* Kustomize
* Multi-environment GitOps
* Progressive delivery

---

# 13. First Application

The first application should be intentionally simple.

Use a small stateless demo workload.

Preferred structure:

```text
apps/
└── demo-app/
    ├── base/
    │   ├── deployment.yaml
    │   ├── service.yaml
    │   └── kustomization.yaml
    │
    └── overlays/
        └── local/
            └── kustomization.yaml
```

Do not over-engineer the first application.

---

# 14. Kustomize

Use Kustomize when environment-specific configuration becomes useful.

Understand:

```text
Base
  |
  +-- common Kubernetes configuration
  |
  +-- Local overlay
        |
        +-- local-specific configuration
```

Do not introduce Kustomize complexity merely for the sake of using it.

---

# 15. Networking

Keep networking simple initially.

Use the networking provided by k3s unless there is a specific learning requirement to change it.

Initially learn:

* Pod networking
* ClusterIP
* NodePort
* LoadBalancer
* Ingress
* DNS
* Service discovery

Do not introduce:

* Service mesh
* Complex CNI changes
* External load balancers
* Complex network policies

unless explicitly requested.

---

# 16. Ingress

Do not immediately replace or customize the k3s default ingress controller.

First understand:

```text
Pod
 |
Service
 |
Ingress
 |
Client
```

Any changes to the default ingress architecture must be explained and documented.

---

# 17. Observability

Initially use native Kubernetes troubleshooting tools:

```bash
kubectl get
kubectl describe
kubectl logs
kubectl events
kubectl top
```

Do not immediately install:

* Prometheus
* Grafana
* OpenTelemetry
* OpenObserve
* Loki
* Elasticsearch

unless required by a specific learning phase.

---

# 18. Security

Follow reasonable security practices even in this local environment.

Principles:

* Least privilege
* No real credentials in Git
* No plaintext secrets
* No unnecessary cluster-admin permissions
* No public exposure of Kubernetes APIs
* No public exposure of Argo CD

Never commit:

```text
kubeconfig
private keys
passwords
tokens
API credentials
real secrets
```

Use placeholders or local-only mechanisms for demonstrations.

---

# 19. Scripts

Automation scripts should be:

* Bash
* Idempotent where practical
* Re-runnable
* Explicit about failures
* Easy to understand

Prefer:

```bash
set -euo pipefail
```

where appropriate.

Scripts must not silently perform destructive actions.

Destructive operations must require explicit confirmation.

---

# 20. Validation

Every implementation phase must include validation.

### k3s

```bash
kubectl get nodes
kubectl get pods -A
```

### Argo CD

```bash
kubectl get pods -n argocd
kubectl get svc -n argocd
```

### GitOps

Verify:

* Argo CD Application exists.
* Application is synced.
* Application is healthy.
* Kubernetes workload exists.
* Git changes are detected.
* Reconciliation works.

Never claim that an installation is successful without validating it.

---

# 21. Troubleshooting

When something fails:

### Step 1

Inspect the current state.

### Step 2

Identify the actual error.

### Step 3

Explain the likely cause.

### Step 4

Make the smallest required change.

### Step 5

Validate again.

### Step 6

Document the resolution if it is useful for future troubleshooting.

Do not immediately:

* Delete the cluster
* Reinstall everything
* Reset Kubernetes
* Remove configuration

unless the evidence indicates that it is necessary and the user approves.

---

# 22. Documentation

Significant changes must be documented.

Documentation should explain:

* What changed
* Why it changed
* How it works
* How to verify it
* How to troubleshoot it
* How to remove it

Commands should be copy/paste friendly.

Avoid undocumented magic.

---

# 23. ADRs

Use Architecture Decision Records for meaningful architectural decisions.

Format:

```text
docs/decisions/
└── ADR-XXX-title.md
```

Each ADR should contain:

```markdown
# Title

## Status

## Context

## Decision

## Alternatives Considered

## Consequences
```

Do not create ADRs for trivial implementation details.

---

# 24. Learning Mode

This project is intended for hands-on learning.

For important components, explain:

```text
What is it?
Why is it needed?
How does it work?
What Kubernetes component is involved?
What does Argo CD do?
How would this work in production?
```

Do not hide important Kubernetes concepts behind automation.

Automation is encouraged, but understanding comes first.

---

# 25. Future Architecture

The project may eventually evolve toward:

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

Potential future topics:

* Backstage
* Application scaffolding
* ApplicationSets
* Multi-environment GitOps
* Policy as Code
* OPA
* Secrets management
* Observability
* Platform Engineering workflows

Do not implement these until the core Kubernetes + Argo CD + GitOps workflow is stable.

---

# 26. Relationship to Platform Engineering

This lab may eventually be used to experiment with concepts relevant to an Enterprise Engineering Platform.

The long-term conceptual model is:

```text
Developer / Platform User
          |
          v
       Backstage
          |
          v
     Git / Desired State
          |
          v
       Argo CD
          |
          v
      Kubernetes
```

However, this local lab should remain intentionally smaller than an enterprise platform.

Do not import enterprise complexity into the lab prematurely.

---

# 27. Engineering Principles

Always prefer:

```text
Simple          > Complex
Declarative     > Imperative
Reproducible    > Manual
Documented      > Implicit
Observable      > Assumed
Least privilege > Excessive privilege
Small changes   > Large changes
```

The goal is a clean, lightweight, reproducible Kubernetes and GitOps laboratory.

---

# 28. Claude Code Behavior

When working on this project:

* Read this file before making changes.
* Inspect before modifying.
* Explain significant changes.
* Avoid unnecessary dependencies.
* Prefer incremental implementation.
* Validate every phase.
* Keep documentation synchronized with implementation.
* Do not assume previous commands succeeded.
* Do not claim success without verification.
* Ask for approval before destructive or system-wide changes.
* Preserve existing user configuration unless explicitly instructed otherwise.
