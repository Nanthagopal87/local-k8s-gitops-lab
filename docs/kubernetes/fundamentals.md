# Kubernetes Fundamentals (Phase 2)

Hands-on exercises on the single-node k3s cluster from [docs/setup/k3s.md](../setup/k3s.md), run on 2026-09-20 in the namespace `k8s-learning`. Every result below was observed on this cluster, not copied from documentation. Argo CD was **not** installed yet when this phase was done.

> **Update (Phase 4):** the long-lived resources from this phase are now managed by Argo CD and their manifests moved to [`argocd/apps/k8s-learning/`](../../argocd/apps/k8s-learning/) (see [gitops-adoption.md](../argocd/gitops-adoption.md)). [`kubernetes/learning/`](../../kubernetes/learning/) keeps only the manual exceptions (the fake Secret and the disposable demos). The sections below describe the Phase 2 work as it happened; paths and commands in section 2 are updated to the current layout.

Manifests were applied with `kubectl apply -f` in Phase 2 to understand the resources before Argo CD managed them.

## Contents

1. [Architecture at a glance](#1-architecture-at-a-glance)
2. [Repository layout and how to re-apply](#2-repository-layout-and-how-to-re-apply)
3. [Namespaces](#3-namespaces)
4. [Pods](#4-pods)
5. [Deployments and ReplicaSets](#5-deployments-and-replicasets)
6. [Services and DNS](#6-services-and-dns)
7. [ConfigMaps](#7-configmaps)
8. [Secrets](#8-secrets)
9. [Resource requests and limits](#9-resource-requests-and-limits)
10. [Probes](#10-probes)
11. [Persistent storage](#11-persistent-storage)
12. [NodePort](#12-nodeport)
13. [Ingress](#13-ingress)
14. [RBAC](#14-rbac)
15. [Environment-specific observations](#15-environment-specific-observations)
16. [Cleanup state and how to remove everything](#16-cleanup-state-and-how-to-remove-everything)
17. [Lessons for the Argo CD phase](#17-lessons-for-the-argo-cd-phase)

---

## 1. Architecture at a glance

```text
Client (curl on WSL, or a Pod)
   |
   |  Host: demo.k8s-learning.test          in-cluster: demo-web.k8s-learning.svc.cluster.local
   v                                                    |
Traefik (Ingress controller, kube-system)               |
   |  reads Ingress rules                               |
   v                                                    v
Service demo-web (ClusterIP 10.43.93.100:80) ---- selector: app.kubernetes.io/name=demo-web
   |  EndpointSlice = the READY Pod IPs
   +--> Pod demo-web-...-bs5mk (10.42.0.x:8080)  \
   +--> Pod demo-web-...-zvk6x (10.42.0.x:8080)   >-- owned by ReplicaSet demo-web-659856bb7c
                                                  /   owned by Deployment demo-web
   each Pod mounts: ConfigMap demo-web-config, Secret demo-web-secret (fake)
   PVC demo-data (local-path) is bound and demonstrated separately, RBAC: ServiceAccount pod-reader
```

Node > Pod > Container: the **node** is the machine (here the one WSL2 VM `<node-name>`), a **Pod** is the smallest schedulable unit (one or more containers sharing an IP and volumes), a **container** is one running image process inside a Pod.

## 2. Repository layout and how to re-apply

```text
argocd/apps/k8s-learning/               # MANAGED BY ARGO CD since Phase 4 (Application "k8s-learning")
├── namespace.yaml
├── demo-web-config.yaml                # ConfigMap
├── demo-web-deployment.yaml            # replicas, probes, resources, config + secret consumption
├── demo-web-service.yaml               # ClusterIP Service
├── demo-web-ingress.yaml
├── demo-data-pvc.yaml
└── pod-reader-rbac.yaml                # ServiceAccount + Role + RoleBinding

kubernetes/learning/                    # manual exceptions, NOT managed by Argo CD
├── pod/pod.yaml                        # bare Pod (disposable demo)
├── secret/demo-web-secret.yaml         # OBVIOUSLY FAKE values
├── service/demo-web-nodeport.yaml      # demo only, not kept running
└── storage/pvc-writer-pod.yaml         # disposable demo Pod that uses the PVC
```

Kustomize was deliberately not used: a flat set of small manifests is clearer at this stage (Kustomize arrives in Phase 5).

Re-create the kept environment from scratch (only needed if the namespace is gone; Argo CD normally does this):

```bash
kubectl apply -f kubernetes/learning/secret/                       # the fake Secret first (manual exception)
kubectl apply -f argocd/applications/k8s-learning.yaml             # then let Argo CD create the rest
# and request a sync in the Argo CD UI/API (manual sync), see docs/argocd/gitops-adoption.md
```

Check the cluster against Git: **use Argo CD's diff, not `kubectl diff`**. In Phase 2 an empty `kubectl diff -f <manifests>` meant "identical". After adoption, Argo CD adds a tracking annotation that is not in the files, so `kubectl diff` on those manifests is no longer empty, and a hand `kubectl apply` would strip it.

Every image is `busybox:1.37` (its built-in `httpd`; a few MB, already cached from Phase 1). Each Pod runs as non-root (uid 65534), drops all capabilities, forbids privilege escalation and uses the `RuntimeDefault` seccomp profile. The page each Pod serves contains its own Pod name, which makes load balancing and rollouts visible.

---

## 3. Namespaces

**What is it?** A named scope that groups resources and lets names repeat across groups.
**Why does it exist?** Isolation and organisation: separate teams, apps or environments in one cluster, with per-namespace RBAC and quotas.
**How does it work?** Most objects live in exactly one namespace. Nodes, PersistentVolumes and Namespaces themselves are cluster-scoped. Service DNS names include the namespace.
**What did we deploy?** `k8s-learning` (label `purpose=learning`). Nothing of ours goes into `kube-system`.
**How did we verify?** `kubectl apply -f namespace.yaml`, then `kubectl get namespace k8s-learning --show-labels` showed `Active`. All 10 other manifests passed `kubectl apply --dry-run=server` before anything was created.
**Remember:** namespaces are an organisation and permission boundary, not a security or network boundary by themselves. Deleting a namespace deletes everything in it.

## 4. Pods

**What is it?** The smallest deployable unit: one or more containers sharing a network identity (one IP) and volumes, scheduled together on one node.
**Why does it exist?** Containers that must run together (for example an app plus a helper) need a shared network namespace and lifecycle; Kubernetes schedules Pods, not bare containers.
**How does it work?** The scheduler picks a node, the kubelet asks containerd to start the containers, and the Pod gets an IP from the Pod network (flannel, `10.42.0.0/16` here). A Pod is ephemeral: it is never healed, moved or restarted onto another node by itself.
**What did we deploy?** `demo-pod` (`pod/pod.yaml`), a bare Pod running busybox `httpd`, deliberately disposable.

**How did we verify?**

| Step | Result |
|---|---|
| `kubectl get pod demo-pod -o wide --show-labels` | `1/1 Running`, IP `10.42.0.13`, node `<node-name>`, labels `app.kubernetes.io/name=demo-pod, tier=disposable` |
| `curl http://10.42.0.13:8080/` from WSL | `hello from demo-pod`. Pod IPs are reachable from the host. |
| `kubectl logs demo-pod` | startup line plus a request log line (`httpd -v`) |
| `kubectl describe pod demo-pod` | Conditions all `True`; events: `Scheduled`, `Pulled` ("already present on machine"), `Created`, `Started` |
| `kubectl get pods -l tier=disposable` | label selector returns the Pod |
| `kubectl delete pod demo-pod` | Pod gone and **not recreated**: it has no `ownerReferences` |

**Remember:** do not run long-lived apps as bare Pods. Use a Deployment (or StatefulSet/DaemonSet/Job) so a controller owns the Pod. In production you almost never create Pods directly.

## 5. Deployments and ReplicaSets

**What is it?** A **Deployment** declares the desired state of a stateless app (image, replicas, update strategy). It creates one **ReplicaSet** per Pod-template revision, and each ReplicaSet keeps N identical Pods running.
**Why does it exist?** Bare Pods are not self-healing. A controller loop constantly compares desired and actual state and corrects the difference: replace deleted Pods, scale, roll out new versions with zero downtime, roll back.
**How does it work?** `spec.selector` picks the Pods the Deployment owns and must match the template labels (it is immutable). The ReplicaSet adds a `pod-template-hash` label so each revision owns a distinct set of Pods. Changing anything under `spec.template` creates a new ReplicaSet and shifts Pods over, governed by `maxSurge` and `maxUnavailable`.
**What did we deploy?** `demo-web` with `replicas: 2`, `RollingUpdate` (`maxSurge: 1`, `maxUnavailable: 0`), `revisionHistoryLimit: 5`.

```text
Deployment demo-web  -> ReplicaSet demo-web-659856bb7c -> Pod demo-web-659856bb7c-bs5mk
                                                       -> Pod demo-web-659856bb7c-zvk6x
```

**How did we verify?**

* Ownership was read from the API: `Pod ... owned by ReplicaSet/demo-web-659856bb7c`, `RS ... owned by Deployment/demo-web`.
* Selectors: Deployment `{app.kubernetes.io/name: demo-web}`, ReplicaSet `{app.kubernetes.io/name: demo-web, pod-template-hash: 659856bb7c}`.
* **Self-healing:** deleting one Pod (`...-klb9f`) made the ReplicaSet create `...-sj77b` within seconds (event `SuccessfulCreate`). Contrast with the bare Pod, which stayed deleted.
* **Scaling** (edit `replicas: 3` in the manifest, `kubectl apply`): the **same** ReplicaSet went to 3 Pods, revision stayed `1`. Scaling does not create a revision. Restored to 2 the same way.
* **Rolling update** (edit `APP_VERSION: "v1"` to `"v2"`, `kubectl apply`): a new ReplicaSet `demo-web-5cf8666bd` (revision 2) appeared, the old one went to 0 but was kept for rollback. The page changed to `version=v2`.
* **Rollback** (`kubectl rollout undo`): the **old** ReplicaSet `659856bb7c` was reused and became revision 3. The clean Pod watch showed the pattern for each step: a new Pod goes `Pending` → `Running` → `Ready=true` **before** an old Pod is removed. That is `maxUnavailable: 0` at work. The page returned to `version=v1`.

**Remember:**

* Changing the Pod template rolls out; changing `replicas` does not.
* `rollout undo` is an imperative escape hatch. It warned that it does not update the `last-applied-configuration` annotation, and it left the cluster (v1) different from the manifest (v2). `kubectl diff -f` showed exactly that drift. The declarative rollback is to **revert the change in the manifest** and apply. After reverting to `v1`, `kubectl diff` was empty and `apply` started no new rollout.
* Temporary drift test: `kubectl scale --replicas=4` (clearly temporary) made the cluster differ from the manifest (`replicas: 2`); re-running `kubectl apply -f` put it back to 2. `apply` overwrites live fields that the manifest specifies. That is what Argo CD's self-heal will do continuously.
* `CHANGE-CAUSE` in `rollout history` is empty unless the `kubernetes.io/change-cause` annotation is set.

## 6. Services and DNS

**What is it?** A stable virtual IP and DNS name in front of a changing set of Pods, chosen by a label selector.
**Why does it exist?** Pod IPs change every time a Pod is replaced. Clients need one address that follows the healthy Pods.
**How does it work?** The Service selector matches Pod labels. The control plane maintains **EndpointSlices** listing the Pod IPs (and whether each is ready). kube-proxy programs the node so traffic to the ClusterIP is spread over the ready endpoints. CoreDNS answers `<service>.<namespace>.svc.cluster.local`.
**What did we deploy?** `demo-web` (`ClusterIP`, port `80` → named container port `http` = 8080).

**How did we verify?**

| Check | Result |
|---|---|
| `kubectl get svc demo-web` | `ClusterIP 10.43.93.100`, `80/TCP`, selector `app.kubernetes.io/name=demo-web` |
| EndpointSlice | `10.42.0.16, 10.42.0.15` (at that time), both `ready=true`, each with a `targetRef` to a Pod; port `8080` |
| `kubectl describe svc` | `Port: http 80/TCP`, `TargetPort: http/TCP`, endpoints on `:8080` |
| DNS from another Pod (temporary `busybox` client) | `demo-web`, `demo-web.k8s-learning.svc.cluster.local` resolve to `10.43.93.100`; `resolv.conf` has `search k8s-learning.svc.cluster.local svc.cluster.local cluster.local`, `nameserver 10.43.0.10` (CoreDNS), `ndots:5` |
| 12 requests to `http://demo-web` | split across both Pods (8 + 4) |
| From the host | `curl http://10.43.93.100/` works (kube-proxy rules are node-wide) |
| Service env vars | `DEMO_WEB_SERVICE_HOST/PORT` present in the Pod |

**Remember:**

* Use DNS, not the ClusterIP. Env-var discovery only includes Services that existed when the Pod started.
* A Service only routes to **ready** Pods (see [Probes](#10-probes)). `kubectl get endpoints` is deprecated in favour of EndpointSlices.
* `busybox nslookup demo-web.k8s-learning` returned `NXDOMAIN`, while `wget http://demo-web.k8s-learning/` worked. Busybox's `nslookup` does not apply the search list to two-label names, but the libc resolver used by real applications does. It is a tool limitation, not a CoreDNS fault. Use `wget`/`curl` for DNS tests in busybox, or a full FQDN.

## 7. ConfigMaps

**What is it?** Non-sensitive configuration stored in the cluster as key/value data.
**Why does it exist?** To keep configuration out of the image so the same image runs in different environments.
**How does it work?** Consumed as environment variables (`configMapKeyRef`) or mounted as files (a `configMap` volume, one file per key). The API stores it in plain text.
**What did we deploy?** `demo-web-config`: `APP_MESSAGE` (used as an env var) and `app.properties` (mounted at `/etc/demo/config`).

**How did we verify?** Inside a Pod: `APP_MESSAGE=hello from a ConfigMap`; `/etc/demo/config/app.properties` contains `log.level=info`; the served page showed `message=hello from a ConfigMap`. Files are symlinks into `..data/`, which is how updates are swapped in atomically.

**Update behaviour, tested:** the ConfigMap was changed (`log.level=debug`, new message) and applied. The **mounted file updated in about 65 s** in the running Pod, but the **env var stayed unchanged** until the Pod restarts. The manifest was then restored to the Git-managed content.

**Remember:** env vars are fixed at container start; mounted files refresh eventually (kubelet sync delay). Changing a ConfigMap does **not** restart Pods. Never store credentials in a ConfigMap: it is plain text, is not access-controlled separately from other config, and tools display it freely.

## 8. Secrets

**What is it?** An object for small sensitive values (passwords, tokens, keys), consumed like a ConfigMap.
**Why does it exist?** So credentials get their own object type: RBAC can be limited to it, values are kept out of `describe` output, and Secret volumes are held in memory.
**How does it work?** Values are stored base64-encoded in the API (and in the datastore; encryption at rest is a separate cluster option). Consumed as env vars (`secretKeyRef`) or files (`secret` volume, mounted on `tmpfs`).
**What did we deploy?** `demo-web-secret`: `username=demo-user`, `password=not-a-real-password`, **obviously fake and learning-only** (label `learning-only=true`, warning header in the file). `DEMO_USER` env var, plus files in `/etc/demo/secret` (mode `0440`, with `fsGroup` so uid 65534 can read).

**How did we verify?** `DEMO_USER=demo-user`; `cat /etc/demo/secret/username` and `password` matched; the mount type was `tmpfs`; `kubectl describe secret` showed only sizes (`password: 19 bytes`).

**base64 is not encryption:** `kubectl get secret demo-web-secret -o jsonpath='{.data.password}'` returned `bm90LWEtcmVhbC1wYXNzd29yZA==`, and `base64 -d` recovered `not-a-real-password` immediately. Anyone who can read the Secret (or this file) has the value.

**Remember:**

* A Kubernetes Secret is **not** a secrets-management system: no rotation, no audit by default, and anyone with read access to Secrets (or `exec` into a Pod) sees everything. Production uses an external manager (Vault, cloud secret store) synced in with a tool such as External Secrets, or sealed/encrypted secrets, plus encryption at rest.
* `.gitignore` blocks common credential files and the patterns `*.secret.yaml`, `*-secret.local.yaml`, `secrets.local/`. The single tracked Secret manifest is the fake one. **Real values must never be committed.**
* Env-var secrets leak into logs, crash dumps and child processes more easily than files; prefer files.

## 9. Resource requests and limits

**What is it?** Per-container CPU and memory numbers.
**Why does it exist?** So the scheduler can place Pods on nodes with room, and so one container cannot starve the others.
**How does it work?** `request` = the amount the scheduler **reserves** for placement (and the guaranteed share under contention). `limit` = the **maximum** allowed: CPU beyond the limit is throttled, memory beyond the limit gets the container OOM-killed. Requests below limits give QoS class `Burstable`; equal requests and limits give `Guaranteed`; none gives `BestEffort`.
**What did we deploy:** `requests: cpu 5m, memory 4Mi`, `limits: cpu 50m, memory 16Mi` on every container, kept small for this 7.5 GiB WSL VM.

**How did we verify?** `kubectl describe pod` showed the values and `QoS Class: Burstable`. `kubectl top pods -n k8s-learning`: about `1m`-`9m` CPU and `0Mi` memory (below the 1 MiB display granularity). The node's total requests (all Pods including system ones) were `cpu 210m (1%)`, `memory 148Mi (1%)`. No pressure was created.

**Remember:** memory limits are hard, so size them from real usage; requests drive scheduling, so over-requesting wastes capacity. CPU limits cause throttling, and many teams set CPU requests only.

## 10. Probes

**What is it?** Health checks the kubelet runs against a container.
**Why does it exist?** A running process is not necessarily working. Probes let Kubernetes stop sending traffic to, or restart, a container that is not healthy.
**How does it work?**

```text
Readiness  = can this Pod receive traffic?     -> failing: removed from Service endpoints, NOT restarted
Liveness   = should this container be restarted? -> failing: container killed and restarted
```

**What did we deploy:** in `demo-web`, HTTP probes, non-aggressive: readiness `GET /ready` (delay 2 s, every 5 s, 3 failures), liveness `GET /healthz` (delay 10 s, every 10 s, 3 failures). The endpoints are files in busybox's web root, so they can be removed on purpose.

**How did we verify (fault injection with `kubectl exec`, a one-off diagnostic):**

| Test | Observed |
|---|---|
| Delete `/tmp/www/ready` in Pod A | Pod A `Ready=false` after about 15 s (3 × 5 s). EndpointSlice showed `ready=false` for it. 10 of 10 requests through the Service went to Pod B. **Restarts stayed 0.** |
| Recreate the file | Pod A Ready again in about 6 s, back in the endpoints. |
| Delete `/tmp/www/healthz` in Pod A | About 30 s later (3 × 10 s) events `Liveness probe failed: ... 404` then `Killing`; the container restarted (`restartCount=1`) in the **same Pod**, which became Ready again. |

**Remember:** a readiness probe protects users (and rolling updates: a new Pod only receives traffic once ready). A liveness probe protects against hangs, so keep it cheap and lenient, because an over-aggressive liveness probe can cause restart loops under load. Use a startup probe for slow-starting apps.

## 11. Persistent storage

**What is it?** Storage that outlives Pods: a **PersistentVolumeClaim** (a request for storage) bound to a **PersistentVolume** (the actual storage), provisioned by a **StorageClass**.
**Why does it exist?** Container filesystems are discarded with the container. Stateful data needs a lifecycle independent of any Pod.
**How does it work?** k3s ships the `local-path` StorageClass (provisioner `rancher.io/local-path`, default, reclaim `Delete`, binding mode `WaitForFirstConsumer`). The provisioner creates a directory on the node when a Pod that uses the claim is scheduled.
**What did we deploy:** PVC `demo-data` (`64Mi`, `ReadWriteOnce`) and the disposable Pod `pvc-writer`, which appends a line to `/data/hello.txt` at each start.

**How did we verify?**

* PVC alone stayed **`Pending`**, with event `waiting for first consumer to be created before binding`, and no PV existed. It was applied first on purpose.
* After `pvc-writer` was applied, the PVC became `Bound` and PV `pvc-51defd3a-...` appeared (`64Mi`, `RWO`, `Delete`).
* The PV points at `local.path=/var/lib/rancher/k3s/storage/pvc-51defd3a-..._k8s-learning_demo-data` and has **node affinity to `<node-name>`**. The Pod saw it as `/dev/sdd ext4` at `/data`, using 8 kB.
* Pod deleted, re-created: `hello.txt` had **two lines** (12:27:09 and 12:27:21). The data outlived the Pod; the PVC stayed `Bound`.

**Remember:**

* `local-path` is **node-local**: the data lives on this node's disk, is bound to it by node affinity, and is not replicated. It is fine for a lab and wrong for production, where you want distributed or network storage (Ceph/Longhorn, EBS/PD, NFS/CSI) with replication and snapshots.
* The requested size is **not enforced**. `Delete` reclaim means deleting the PVC deletes the data. The kept PVC `demo-data` still holds the two lines.

## 12. NodePort

**What is it?** A Service type that also opens a port (default range 30000-32767) on every node.
**Why does it exist?** The simplest way to reach a Service from outside the cluster without a cloud load balancer.
**How does it work?** A NodePort Service is a ClusterIP Service plus a node port. Traffic to `<any node IP>:<nodePort>` is forwarded to the Service's ready Pods.

```text
ClusterIP = internal cluster access         NodePort = node-level access from outside
```

**What did we deploy:** `demo-web-nodeport`, port `30080` (free, not Traefik's `30819`/`31320`, and **not 80/443**), same selector as the ClusterIP Service.

**How did we verify?** `curl http://127.0.0.1:30080/` and `http://<WSL-IP>:30080/` (the WSL `eth0` address), six requests each, split evenly across both Pods. Both Services had their own EndpointSlice with the same two Pod IPs. `ss -ltn` showed no listening socket for 30080; NodePorts appear to be implemented by kernel rules from kube-proxy (inferred; not inspected with root). nginx and host ports 80/443 were unchanged.

**Remember:** NodePort exposes the port on all node interfaces, so it is for demos and for bootstrapping (Traefik itself uses NodePorts). The demo Service was **deleted at the end**; the manifest is kept for reference. Production uses LoadBalancer or Ingress instead.

## 13. Ingress

**What is it?** **Ingress** is an API object with HTTP routing *rules* (host/path → Service). An **Ingress controller** is the software that reads those rules and does the routing.
**Why does it exist?** To share one entry point across many Services with host/path-based routing and TLS termination, instead of one NodePort or LoadBalancer per Service.
**How does it work?** An Ingress does nothing by itself. k3s installs **Traefik** (Deployment in `kube-system`, exposed through the `traefik` LoadBalancer Service, with `IngressClass traefik` marked default). Traefik watches Ingress objects and configures itself; it forwards to Services (via their endpoints), which route to Pods.

```text
Client -> Ingress controller (Traefik) -> [Ingress rule: host+path] -> Service -> Pod
```

| Term | Meaning |
|---|---|
| Ingress resource | The rules (an object in the API) |
| Ingress controller | The running proxy that implements the rules (Traefik) |
| Service | Stable name/IP and load balancing over Pods |
| Pod | The workload |

**What did we deploy:** Ingress `demo-web`, `ingressClassName: traefik`, host `demo.k8s-learning.test` (`.test` is reserved and never resolves publicly), path `/` → Service `demo-web:http`. **Traefik and nginx were not modified** (Traefik Deployment `generation=1` right after the Ingress was applied and again at the end of the phase; nginx config files keep their original timestamps).

**How did we verify?** The Ingress got `ADDRESS <WSL-IP>` (the ServiceLB IP) and `describe` showed the backend Pod IPs.

* **Through Traefik's NodePort** (`30819`), with name resolution supplied by `curl --resolve demo.k8s-learning.test:30819:127.0.0.1`: served by both Pods (3 + 3). No `/etc/hosts` edit and no dependence on host ports 80/443.
* Host header only, via Traefik's ClusterIP `10.43.90.246:80`: served by the app.
* No `Host` header, or a different host: Traefik's own `404`, because rules are host-based.

**Relation to the Phase 1 nginx/Traefik conflict.** nginx and Traefik both want host ports 80/443, and Traefik currently receives that traffic ([docs/setup/k3s.md section 7](../setup/k3s.md#7-nginx-on-ports-80443)). This phase changed nothing there. A supplementary read-only request `curl -H 'Host: demo.k8s-learning.test' http://127.0.0.1:80/` also reached our app, but only *because* Traefik owns host port 80 right now (the conflict). That is fragile: if the conflict is resolved in favour of nginx, that path stops working. Use the NodePort path or `kubectl port-forward` for tests until that decision is made.

**Update (Phase 2.6): the conflict is resolved.** Traefik now uses host ports **8880/8843** ([ADR-001](../decisions/ADR-001-traefik-alternate-host-ports.md)), and nginx owns 80/443 again. Consequences for the tests above: `Host: demo.k8s-learning.test` sent to `127.0.0.1:80` now reaches nginx (a `301`), no longer this app. The Ingress test through Traefik's Service ClusterIP must use `10.43.90.246:8880` instead of `:80`. The Traefik NodePort path (`30819`) is unchanged, and the new local entry points are `http://demo.k8s-learning.test:8880` and `https://...:8843` (use `curl --resolve`, as above).

**Remember:** in production you also need TLS certificates (for example cert-manager), one Ingress controller shared across the cluster, and a real DNS record pointing at its load balancer. The newer Gateway API is the successor to Ingress.

## 14. RBAC

**What is it?** Role-based access control for the Kubernetes API.
**Why does it exist?** So each user, service or CI system gets only the permissions it needs (least privilege).
**How does it work?** A **subject** (user, group, **ServiceAccount**) is granted a **Role** (verbs on resources, namespaced) through a **RoleBinding**. `ClusterRole`/`ClusterRoleBinding` do the same cluster-wide. RBAC is additive and deny-by-default: no rule means no access.
**What did we deploy:** in `k8s-learning`: ServiceAccount `pod-reader` (token automount disabled), Role `pod-reader` (`get, list, watch` on `pods` only), RoleBinding `pod-reader`. No ClusterRole, no cluster-admin, no Secret access, no write verbs.

**How did we verify** with `kubectl auth can-i ... --as=system:serviceaccount:k8s-learning:pod-reader`:

| Action | Result |
|---|---|
| get / list / watch pods in `k8s-learning` | yes |
| delete pods, create pods | **no** |
| get secrets in `k8s-learning` | **no** |
| get pods in `kube-system`, across all namespaces | **no** |
| get nodes (cluster-scoped) | **no** |
| `default` ServiceAccount: get pods | no (the default SA has no permissions) |

Real requests as that identity agreed: `get pods` listed the Pods, `delete pod` failed with `Forbidden ... cannot delete resource "pods"`, `get secrets` failed with `Forbidden`, and the target Pod survived. `ClusterRoleBindings` mentioning `pod-reader`: 0.

**Remember:** our `~/.kube/config` identity is `system:admin` (cluster-admin), which bypasses all of this, so always test permissions with `--as`. Give workloads their own ServiceAccount and disable token automount when the app does not use the API (as `demo-web` does). Argo CD will need broad rights, so its scope should be a deliberate decision.

---

## 15. Environment-specific observations

* **Busybox `httpd` ignores SIGTERM** when it is PID 1, so Pods only die after the grace period. `terminationGracePeriodSeconds: 5` keeps demos fast. Terminating Pods showed phase `Failed` briefly, consistent with being SIGKILLed after the grace period (the exit code was not inspected). Real apps should handle SIGTERM.
* **Busybox `nslookup` and two-label names** (see [Services](#6-services-and-dns)).
* **Pod IPs, ClusterIPs and NodePorts are reachable from WSL itself** (the host shares the node's network namespace and kube-proxy rules), which makes testing easy. Pod IPs change on every recreate.
* **`kubectl top` shows `0Mi`** for tiny Pods (below 1 MiB granularity). Node-level memory is a WSL-wide number (76%), not a Kubernetes number.
* **First start of a Pod that needs a PVC** appears as `Pending` PVC until scheduling (`WaitForFirstConsumer`). Expect the same in Argo CD health views.
* **Cost of Phase 2 on this VM:** negligible. Before: `used 5.1 GiB, available 2.5 GiB` (baseline 5.0-5.1 / 2.5). During and after the kept environment (2 Pods): `used 5.1 GiB, available 2.4 GiB`, node CPU about 0.3-0.5 cores. Swap moved from about 708 to 771 MiB, but `used` was unchanged, so that drift comes from other WSL workloads. Kept `demo-web` Pods use about 1m CPU and under 1 MiB each.
* **`ss` and sudo:** nothing in this phase needed sudo; all checks ran as the normal user through the `kubectl` wrapper.
* **Nothing on the host changed:** nginx `active` with unchanged config timestamps, host ports 80/443 unchanged, Traefik and ServiceLB unmodified.

## 16. Cleanup state and how to remove everything

Kept running on purpose (small, useful for later exercises):

| Kept | Why |
|---|---|
| Namespace `k8s-learning` | A home for future exercises |
| Deployment `demo-web` (2 Pods), Service `demo-web`, Ingress `demo-web` | The working reference app, about 1m CPU and under 1 MiB per Pod |
| ConfigMap `demo-web-config`, Secret `demo-web-secret` (fake) | Referenced by the Deployment |
| PVC `demo-data` (`64Mi`, 8 kB used) | Storage demo, tiny |
| ServiceAccount, Role, RoleBinding `pod-reader` | RBAC demo, no cost |

Removed after use: the bare Pod `demo-pod`, Pod `pvc-writer`, and Service `demo-web-nodeport` (NodePort `30080` no longer answers). Temporary busybox client Pods were auto-removed (`--rm`). Their manifests remain in the repo.

Remove the kept environment when no longer wanted (this **deletes the PVC data** and everything in the namespace):

```bash
kubectl delete namespace k8s-learning
```

Or remove selectively, for example to free the two Pods but keep the config: delete the Application-managed resources through Git (or `kubectl delete -f argocd/apps/k8s-learning/demo-web-deployment.yaml`, which Argo CD will then report as `OutOfSync`). Removing these resources does not affect k3s system components.

## 17. Lessons for the Argo CD phase

1. **Git is the source of truth; anything else is drift.** `kubectl scale` and `kubectl rollout undo` made the cluster differ from the manifests, and `kubectl diff` showed it. Argo CD's *sync status* is this diff, and *self-heal* is what a re-`apply` did by hand.
2. **A rollback in GitOps is a Git revert**, not `rollout undo`. `undo` also does not update the last-applied annotation.
3. **ConfigMap/Secret changes do not restart Pods, and env vars never refresh.** A pure config change in Git will not roll a Deployment by itself. A checksum annotation or Kustomize `configMapGenerator` (Phase 5) solves it.
4. **No real secrets in Git, ever.** The fake Secret is a teaching artefact. A real approach (sealed or external secrets) is required before any actual credential is involved.
5. **Order and dependencies matter:** the namespace, ConfigMap and Secret must exist before the Deployment that uses them (Pods would sit in `CreateContainerConfigError`, not exercised here). Argo CD uses sync waves for ordering.
6. **Health is more than "created":** a PVC is `Pending` until first use, an Ingress needs an address, and a Deployment needs ready Pods. Argo CD reports these as `Progressing` before `Healthy`.
7. **Do not hand disposable manifests to Argo.** `pod/pod.yaml`, `storage/pvc-writer-pod.yaml` and the NodePort manifest were meant to be temporary. The real demo app will live under `apps/demo-app/` with a base and a `local` overlay.
8. **Test Ingress through Traefik's own ports** (`8880`/`8843`, or NodePort `30819`; the host-port conflict with nginx was resolved in Phase 2.6), and use `kubectl port-forward` on `127.0.0.1` for the Argo CD UI, which also gives Windows a real `localhost` listener. From Windows, Traefik itself is reachable via the WSL IP, not `localhost:8880`.
9. **Least privilege applies to Argo CD too:** its ServiceAccount and AppProject should be scoped, and the API server must not be exposed.
10. **Memory headroom:** about 2.4 GiB available with swap in use. Argo CD's several components will use a noticeable part of that. Re-measure before and after installing.
