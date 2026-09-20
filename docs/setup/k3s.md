# k3s Setup (Phase 1)

Single-node k3s installed directly inside WSL2 Ubuntu. No Docker, Podman, kind, k3d, Minikube or other runtime was introduced.

Status: **installed and healthy.** The nginx 80/443 conflict found here was **resolved in Phase 2.6** by moving Traefik to host ports 8880/8843 (see [section 7](#7-nginx-on-ports-80443) and [ADR-001](../decisions/ADR-001-traefik-alternate-host-ports.md)).

---

## 1. Environment

| Item | Value |
|---|---|
| Host | Windows + WSL2 (distro `Ubuntu`) |
| OS | Ubuntu 24.04.2 LTS |
| Kernel | 6.6.87.2-microsoft-standard-WSL2 |
| systemd | Enabled via `/etc/wsl.conf` (`[boot] systemd=true`) |
| cgroups | v2 |
| CPU / RAM | 14 vCPU / 7.5 GiB RAM, 2 GiB swap (default WSL limits, no `.wslconfig`) |
| Installed on | 2026-09-20 |

Prerequisites verified before install: systemd running, cgroup v2 with memory and pids controllers, `overlay`/`vxlan` built into the kernel, `br_netfilter` loadable, outbound HTTPS to `get.k3s.io` and `github.com`, no existing k3s, kubeconfig, or Argo CD.

## 2. Versions

| Component | Version |
|---|---|
| k3s | `v1.36.4+k3s1` (stable channel at install time) |
| Kubernetes | `v1.36.4+k3s1` |
| containerd (embedded) | `2.3.4-k3s1.36` |
| CoreDNS | `rancher/mirrored-coredns-coredns:1.14.6` |
| local-path-provisioner | `rancher/local-path-provisioner:v0.0.37` |
| metrics-server | `rancher/mirrored-metrics-server:v0.9.0` |
| Traefik | `rancher/mirrored-library-traefik:3.7.8` (installed by a Helm-controller job) |
| ServiceLB (klipper-lb) | `rancher/klipper-lb:v0.4.17` |

## 3. What was installed and how

Default k3s server, **all default components kept** (Traefik and ServiceLB included).

The installer was downloaded, read, and only then run. It was not piped into a shell:

```bash
curl -sfL https://get.k3s.io -o /tmp/k3s-install.sh
less /tmp/k3s-install.sh
sudo INSTALL_K3S_CHANNEL=stable INSTALL_K3S_SYMLINK=skip sh /tmp/k3s-install.sh
mkdir -p ~/.kube
sudo install -m 600 -o "$USER" -g "$USER" /etc/rancher/k3s/k3s.yaml ~/.kube/config
```

Files created by the installer (all k3s-owned):

| Path | Purpose |
|---|---|
| `/usr/local/bin/k3s` | The single k3s binary (sha256-verified by the installer) |
| `/usr/local/bin/k3s-killall.sh` | Stops k3s and all its containers |
| `/usr/local/bin/k3s-uninstall.sh` | Uninstaller (see [Uninstall](#10-uninstall-procedure)) |
| `/etc/systemd/system/k3s.service` (+ `.env`) | systemd unit, enabled |
| `/etc/rancher/k3s/k3s.yaml` | Cluster admin kubeconfig, root-only (mode 600) |
| `/var/lib/rancher/k3s/` | Data dir: datastore (SQLite), images, containerd state |

### Why `INSTALL_K3S_SYMLINK=skip`

By default the installer symlinks `kubectl`, `crictl` and `ctr` into `/usr/local/bin`. There was already a **dangling symlink** there:

```text
/usr/local/bin/kubectl -> /mnt/wsl/docker-desktop/cli-tools/usr/local/bin/kubectl
```

It is left over from Docker Desktop's WSL integration, and its target does not exist while Docker Desktop is not running. Because a dangling link fails the installer's `-e` test, the stock installer would have silently overwritten it with `ln -sf`. `SYMLINK=skip` avoids modifying a Docker Desktop artifact.

Consequences:

* `crictl` and `ctr` are not on `PATH`. Use `k3s crictl ...` and `k3s ctr ...` (both need `sudo`).
* `kubectl` is provided by a user-local wrapper in `~/.local/bin` instead (next section).
* If Docker Desktop's WSL integration is started later, it may recreate that `/usr/local/bin/kubectl` link. Because `~/.local/bin` comes first on `PATH`, the wrapper still wins.

### Why Traefik and ServiceLB were kept

CLAUDE.md (sections 8 and 16) says to use the default k3s architecture first and not customise ingress before understanding it. Traefik (ingress controller) and ServiceLB (implements `type: LoadBalancer` on a single node) are needed to learn the Pod → Service → Ingress → Client path. They are cheap (about 20 MiB for Traefik). The port 80/443 conflict this causes is documented in [section 7](#7-nginx-on-ports-80443).

## 4. kubectl and kubeconfig

### kubectl (user-local wrapper)

`kubectl` is a small executable wrapper at `~/.local/bin/kubectl`:

```bash
#!/usr/bin/env bash
# kubectl wrapper for the k3s-bundled kubectl (local-k8s-gitops-lab, see docs/setup/k3s.md).
#
# `k3s kubectl` forces /etc/rancher/k3s/k3s.yaml (root-only) when KUBECONFIG is unset.
# Default to the user-owned kubeconfig instead, but respect an explicit KUBECONFIG.
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
exec /usr/local/bin/k3s kubectl "$@"
```

Created with mode 755 (`chmod 755 ~/.local/bin/kubectl`). `~/.local/bin` is already first on `PATH` through the stock `~/.profile`, so no shell configuration was changed. The client is the k3s-bundled kubectl (`v1.36.4+k3s1`, Kustomize v5.8.1).

How it behaves:

* `KUBECONFIG` unset: uses `~/.kube/config`.
* `KUBECONFIG` set (any path): that value is used unchanged, with no fallback.
* `exec` replaces the wrapper process, so signals and exit codes pass straight through.

#### Why a wrapper

| Requirement | How the wrapper meets it |
|---|---|
| No shell configuration changes | Lives in `~/.local/bin`, which is already on `PATH`. Nothing added to `~/.profile` or `~/.bashrc`. |
| No new tool download | Runs the kubectl already bundled in the k3s binary. |
| Same client as the server | Client and server are both `v1.36.4+k3s1`. |
| Respects an explicit `KUBECONFIG` | Only sets the default when unset. |
| Kubeconfig stays user-owned | Keeps using `~/.kube/config` (mode 600). The root-only `/etc/rancher/k3s/k3s.yaml` is not made readable. |

History: the first attempt was a symlink (`~/.local/bin/kubectl -> /usr/local/bin/k3s`). It failed because of the k3s behaviour described below, so it was replaced with the wrapper.

### Kubeconfig

* Location: `~/.kube/config`
* Permissions: `-rw------- nantha:nantha` (600), directory `~/.kube` is 755
* Server: `https://127.0.0.1:6443`. The API server listens on all interfaces inside WSL2 (`*:6443`, as does the kubelet on `*:10250`). Do not forward or publish these ports outside the machine.
* Never commit this file.

### Why the wrapper is needed: k3s `kubectl` ignores `~/.kube/config`

When `KUBECONFIG` is unset, the k3s-bundled `kubectl` **forces** `/etc/rancher/k3s/k3s.yaml`, which is root-only. Called directly (or through a symlink to k3s) it fails:

```text
level=warning msg="Unable to read /etc/rancher/k3s/k3s.yaml, please start server with --write-kubeconfig-mode ..."
error: error loading config file "/etc/rancher/k3s/k3s.yaml": open ...: permission denied
```

`k9s` and any standalone `kubectl` binary read `~/.kube/config` natively and are not affected.

Alternatives that were rejected:

* `--write-kubeconfig-mode 644`: makes the admin credential world-readable.
* `export KUBECONFIG=...` in `~/.profile` or `~/.bashrc`: changes shell configuration.
* Standalone `kubectl` binary: an extra download, and one more version to keep in step with k3s.

## 5. Service management

k3s runs as `k3s.service` (Type=notify), enabled, so it **starts every time WSL starts**.

```bash
systemctl status k3s --no-pager
systemctl is-enabled k3s
systemctl is-active k3s
journalctl -u k3s -f                     # logs

sudo systemctl stop k3s                  # stops the control plane only
sudo /usr/local/bin/k3s-killall.sh       # stops k3s AND all pod containers (frees the memory)
sudo systemctl start k3s
sudo systemctl disable k3s               # stop auto-start with WSL (re-enable with: enable)
```

`k3s.service` uses `KillMode=process`, so `systemctl stop k3s` alone leaves the container shims running. Use `k3s-killall.sh` to fully release resources. State is kept (see uninstall to remove it).

## 6. Validation results (2026-09-20)

Run as the normal user through the `~/.kube/config`-defaulting `kubectl` wrapper, with `KUBECONFIG` unset in the shell.

| Check | Result |
|---|---|
| `command -v kubectl` | `/home/nantha/.local/bin/kubectl` (wrapper) |
| `kubectl version --client` | `v1.36.4+k3s1`, Kustomize `v5.8.1` |
| `kubectl config current-context` | `default` |
| Explicit `KUBECONFIG="$HOME/.kube/config" kubectl get nodes` | Works. An explicit nonexistent path is respected, with no silent fallback. |
| Exit code passthrough | `kubectl get pod does-not-exist` exits `1` |
| `systemctl is-enabled k3s` / `is-active k3s` | `enabled` / `active` |
| `kubectl get nodes -o wide` | `laptop-iqneogsk` **Ready**, `control-plane`, `v1.36.4+k3s1`, `containerd://2.3.4-k3s1.36` |
| `kubectl wait --for=condition=Ready node --all` | condition met |
| CoreDNS | `1/1 Running`; in-cluster name resolution of a Service name worked |
| local-path-provisioner | `1/1 Running`; StorageClass `local-path` (default), `WaitForFirstConsumer` |
| metrics-server | `1/1 Running`; `kubectl top nodes` and `kubectl top pods -A` work |
| Traefik | `1/1 Running`; Service `LoadBalancer`, EXTERNAL-IP `172.21.25.138`. At install: ports `80:30819`, `443:31320`. **Since Phase 2.6: `8880:30819`, `8843:31320`** ([section 7](#resolution-phase-26)) |
| ServiceLB | DaemonSet `svclb-traefik-*` `2/2 Running`. At install: `lb-tcp-80`, `lb-tcp-443` with hostPort 80/443. **Since Phase 2.6: hostPort 8880/8843.** |
| Helm jobs | `helm-install-traefik` and `helm-install-traefik-crd` `Completed` (the Traefik one restarted once) |
| Embedded containerd | `sudo k3s crictl version`: `RuntimeName: containerd`, `RuntimeVersion: v2.3.4-k3s1.36`. `crictl info`: `RuntimeReady: true`, `NetworkReady: true`, default runtime `runc` (`io.containerd.runc.v2`). `crictl ps`: 6 containers `Running` (coredns, local-path-provisioner, metrics-server, traefik, `lb-tcp-80`, `lb-tcp-443`). The node also reports `containerd://2.3.4-k3s1.36`. |
| API as normal user | `kubectl cluster-info` OK; `auth can-i get nodes` -> `yes`; identity `system:admin` (cluster-admin) |
| Smoke test | Passed (below) |

Commands:

```bash
command -v kubectl
kubectl version --client
kubectl config current-context
kubectl wait --for=condition=Ready node --all --timeout=120s
kubectl get nodes -o wide
kubectl get pods -A -o wide
kubectl get svc -A
kubectl get namespaces
kubectl get storageclass
kubectl top nodes
kubectl cluster-info
kubectl auth can-i get nodes
kubectl get events -A --sort-by=.lastTimestamp
ls -l ~/.kube/config
```

Containerd check (needs root because the socket `/run/k3s/containerd/containerd.sock` is `root:root 660`; `crictl` is not on `PATH` because of `INSTALL_K3S_SYMLINK=skip`):

```bash
sudo /usr/local/bin/k3s crictl version
sudo /usr/local/bin/k3s crictl info | grep -B4 -E '"type": "(RuntimeReady|NetworkReady)"'   # both "status": true
sudo /usr/local/bin/k3s crictl ps
```

`crictl info` prints the whole runtime configuration (about 200 lines). Filter it as above unless you need the detail.

### Smoke test

A temporary Deployment + ClusterIP Service (busybox HTTP server, own namespace `smoke-test-temp`, label `purpose=temporary-validation`) verified scheduling, startup, readiness probe, in-cluster DNS (`smoke.smoke-test-temp.svc.cluster.local`), Service routing from a pod, and host to ClusterIP. It was deleted afterwards and no namespace, pod, Service or PVC remained.

Its first run failed with `CrashLoopBackOff`. The cause was a bug in the test manifest (non-root user could not `mkdir /`), not a cluster fault. It was fixed and re-run.

### Warnings seen at first start (transient)

Within the first ~2 minutes, and gone afterwards:

* `InvalidDiskCapacity: invalid capacity 0 on image filesystem` (node)
* `FailedCreatePodSandBox ... open /run/flannel/subnet.env: no such file or directory` for CoreDNS, local-path-provisioner and metrics-server. Flannel had not written its subnet file yet; kubelet retried.
* Readiness probe failures on CoreDNS and metrics-server while they started.

These are normal k3s bootstrap noise. Do not treat them as a fault unless the pods stay unready.

## 7. nginx on ports 80/443

**Status: resolved in Phase 2.6.** The finding below is the original Phase 1 state (before the fix); the fix is in [Resolution](#resolution-phase-26).

**Original finding (before the fix):**

* nginx already uses host ports 80 and 443 (`/etc/nginx/sites-enabled/backstage`: port 80 redirects to HTTPS, port 443 terminates TLS and proxies to Backstage on `localhost:7007`).
* k3s's default Traefik + ServiceLB also use host ports 80 and 443.
* Traefik currently receives traffic on those ports that previously reached nginx.
* nginx must not be modified as part of Phase 1, and it was not. Neither were Traefik, ServiceLB, iptables/nftables, host ports or WSL networking.
* Re-checked at the end of Phase 1 validation: nginx `active`, `:80` still returns the Traefik 404, `:443` still presents `CN=TRAEFIK DEFAULT CERT`.

### Observed behaviour

| Observation | Result |
|---|---|
| ServiceLB pod | Scheduled and `2/2 Running`; containers request `hostPort` 80 and 443. **No scheduling or bind error, no warning event.** |
| Traefik Service | `LoadBalancer`, `EXTERNAL-IP 172.21.25.138` (the WSL `eth0` address) |
| nginx | Still `active`, still shows `LISTEN 0.0.0.0:80` and `0.0.0.0:443` in `ss` |
| `GET http://127.0.0.1:80/`, `10.255.255.254:80`, `172.21.25.138:80` | All return Traefik's `404 page not found` (`text/plain`). nginx's config would return a `301` redirect. |
| TLS on `127.0.0.1:443` | Certificate is **`CN=TRAEFIK DEFAULT CERT`**. nginx is configured with the `CN=localhost` cert. |
| Traefik NodePort `:30819` | Returns the identical Traefik 404, confirming that is what answers on `:80` |
| Backstage backend directly (`127.0.0.1:3000`) | Unaffected (HTTP 200) |
| PostgreSQL, OpenObserve, OPA, OTel collector, nginx service | All still active |

### Conclusion

The conflict does not stop k3s or ServiceLB. Instead, **Traefik now shadows nginx on ports 80 and 443** for traffic to localhost and the other local addresses tested. nginx is still running and still holds its listening sockets, but requests never reach it, so the existing `https://localhost/` route to Backstage returns Traefik's 404 while k3s is running. Backstage itself is fine on its own ports.

Mechanism (inferred at the time, **verified in Phase 2.6**, see Root cause below): ServiceLB's pods use `hostPort`, which the CNI implements as iptables DNAT rules that rewrite the destination before the packet reaches nginx's socket. The verification command was:

```bash
sudo /var/lib/rancher/k3s/data/current/bin/aux/iptables-save -t nat | grep -E 'dpt:(80|443)|dport (80|443)'
```

(The bundled iptables path can differ between versions. Find it with `sudo ls /var/lib/rancher/k3s/data/*/bin/aux/`.)

Stopping k3s (`k3s-killall.sh`) removes these rules and returns ports 80/443 to nginx. This was not tested.

### Root cause (Phase 2.6 inspection)

Traefik itself holds no host ports (no `hostNetwork`, no `hostPort`; it listens on container ports 8000/8443, and the Service maps 80 to `web`:8000 and 443 to `websecure`:8443). The host ports came from **ServiceLB**: the DaemonSet `svclb-traefik-*` has containers `lb-tcp-80` and `lb-tcp-443` with `hostPort` 80/443 and `DEST_IPS` set to Traefik's ClusterIP.

```text
client -> host:80/443 --(hostPort NAT rule)--> svclb pod --(klipper DNAT)--> Traefik ClusterIP --> Traefik pod :8000/:8443
```

`hostPort` is implemented with NAT rules, not a listening socket. So nothing failed to bind, `ss` still showed nginx listening, and ServiceLB reported success, but the NAT rewrote the destination before nginx's socket was consulted. Evidence: a uniquely-marked `GET` to `http(s)://localhost` was answered by Traefik and produced **0 lines** in nginx's access log (from WSL and from Windows `curl.exe`). The mechanism was then **verified with root** (`iptables-save -t nat`, captured before the fix): `PREROUTING` and `OUTPUT` (for local destinations) jump to `CNI-HOSTPORT-DNAT`, which DNATs `--dport 80`/`443` to the svclb pod (`10.42.0.7`), including for `127.0.0.1` sources. The same capture showed nginx holding the listening sockets.

### Resolution (Phase 2.6)

Option A1 from the evaluation: keep Traefik a `LoadBalancer` behind ServiceLB, move its external ports to **8880/8843** with a `HelmChartConfig` ([`kubernetes/platform/traefik/helmchartconfig.yaml`](../../kubernetes/platform/traefik/helmchartconfig.yaml)). Rationale and rejected options are in [ADR-001](../decisions/ADR-001-traefik-alternate-host-ports.md). No nginx config, k3s config file, iptables or WSL setting was touched.

```bash
kubectl apply --dry-run=server -f kubernetes/platform/traefik/helmchartconfig.yaml
kubectl apply -f kubernetes/platform/traefik/helmchartconfig.yaml
```

Effect, measured: Service ports became `8880:30819` and `8843:31320` and the `svclb-traefik` Pod was replaced with hostPorts 8880/8843, all within about 12 seconds; nginx answered on `localhost:80` in the same window. Traefik's own Pod was not restarted (Deployment `generation=1`, 0 restarts).

| Check | Result |
|---|---|
| `http://localhost/` | nginx `301` to `https://localhost/` (`Server: nginx/1.24.0`) |
| Certificate on `localhost:443` | `CN=localhost` (nginx's), no longer `TRAEFIK DEFAULT CERT` |
| Marked request in `/var/log/nginx/access.log` | present (3 matches, versus 0 before) |
| `https://localhost/` via nginx vs Backstage backend `127.0.0.1:7007` | identical (`404`, 0 bytes; that root-path response comes from the backend) |
| Windows `curl.exe` on `http(s)://localhost` | nginx (`301` / proxied response) |
| Pods holding hostPort 80/443 | none; `svclb-traefik` holds 8880/8843 |
| Ingress `demo-web` through Traefik | `curl --resolve demo.k8s-learning.test:8880:127.0.0.1 http://demo.k8s-learning.test:8880/` works; HTTPS on `8843` (`-k`, Traefik default cert) works; load balanced over both Pods |
| Old NodePort `30819` | still works |
| Ingress `ADDRESS` | still `172.21.25.138` |
| nginx config hashes, `/etc/rancher/k3s`, nginx/k3s start times | identical to before |

Where Traefik is reachable now:

| From | HTTP | HTTPS |
|---|---|---|
| WSL `localhost` | `http://localhost:8880` | `https://localhost:8843` |
| Windows, via the WSL IP (currently `172.21.25.138`, can change after a WSL restart) | `http://172.21.25.138:8880` | `https://172.21.25.138:8843` |
| Windows `localhost:8880` | **does not work** (verified `000`) | not tested, same reason |

Windows `localhost` forwarding only carries ports with a real listening socket. `hostPort` and NodePort are NAT rules, so use the WSL IP, or `kubectl port-forward` (a real listener) when Windows `localhost` is needed. Ingress rules match on the `Host` header, so send it (`curl --resolve` or `-H 'Host: ...'`) or use a hosts-file entry.

Caveats:

* The Traefik **Service** ports are now 8880/8843. Anything addressing the Service by port 80/443, for example the Phase 2 test through Traefik's ClusterIP (`10.43.90.246:80`), must use `:8880` (see the update note in [fundamentals.md](../kubernetes/fundamentals.md)).
* ServiceLB `hostPort`s can still intercept any host application later bound to 8880/8843. Ports 8080/8443 were avoided on purpose (nginx's Keycloak upstream is `localhost:8080`).
* Fallback if this ever bites: `service.spec.type: NodePort` with fixed nodePorts (Option A2 in the ADR), at the cost of an empty Ingress address.

Rollback: `kubectl delete -f kubernetes/platform/traefik/helmchartconfig.yaml` (Traefik returns to 80/443 and shadows nginx again).

## 8. Resource impact

Measured against the Phase 0 baseline (same WSL instance; other workloads fluctuate, so treat as approximate).

| | Before k3s | After k3s (settled, ~6 min) | Change |
|---|---|---|---|
| Used | 4.4 GiB | 5.0 GiB | **+0.6 GiB** |
| Free | 2.3 GiB | 0.2 GiB | -2.1 GiB |
| Buff/cache | 1.1 GiB | 2.7 GiB | +1.6 GiB (reclaimable, mostly image pulls) |
| Available | 3.1 GiB | 2.5 GiB | **-0.6 GiB** |
| Swap used | 612 MiB | 709 MiB | +97 MiB |

Other measurements:

* `k3s.service` cgroup: 1.43 GiB current, 1.53 GiB peak (includes page cache).
* Resident memory of k3s-server + containerd + shims: about 0.9 GiB.
* Pods: CoreDNS 13 Mi, local-path 8 Mi, metrics-server 21 Mi, Traefik 20 Mi, svclb about 0.
* CPU: 0.4 to 0.5 cores (3%) at idle just after start. Load average about 0.4.
* `kubectl top nodes` reports 76% memory for the whole node (it includes every WSL process, not only k3s).

About 2.5 GiB is available. That is enough for a small Argo CD but not generous. Watch `free -h` and swap.

## 9. Troubleshooting

| Symptom | Check / fix |
|---|---|
| `permission denied` on `/etc/rancher/k3s/k3s.yaml` | You are running `k3s kubectl` (or a symlink to k3s) directly, not the wrapper. Check `command -v kubectl` shows `~/.local/bin/kubectl` ([section 4](#why-the-wrapper-is-needed-k3s-kubectl-ignores-kubeconfig)) |
| `kubectl: command not found` | `ls -l ~/.local/bin/kubectl` (must be an executable script); `hash -r`; open a new login shell |
| `kubectl` uses the wrong cluster or `localhost:8080` refused | An explicit `KUBECONFIG` is set to something else. `echo "$KUBECONFIG"`; `unset KUBECONFIG` |
| `The connection to the server 127.0.0.1:6443 was refused` | `systemctl is-active k3s`; `journalctl -u k3s -n 100 --no-pager` |
| Node `NotReady` | `kubectl describe node`; `journalctl -u k3s`; check `free -h` for memory pressure |
| Pods stuck `ContainerCreating` at start | Usually flannel starting up; wait a minute, then `kubectl describe pod` |
| `https://localhost` shows a Traefik 404 / `TRAEFIK DEFAULT CERT` again | Traefik has taken 80/443 back: check `kubectl get helmchartconfig traefik -n kube-system` still exists and `kubectl get svc traefik -n kube-system` shows `8880`/`8843` ([section 7](#resolution-phase-26)). Re-apply `kubernetes/platform/traefik/helmchartconfig.yaml` |
| Ingress does not answer on `localhost:8880` from Windows | Expected: use the WSL IP (`ip -brief addr show eth0`) or `kubectl port-forward` ([section 7](#resolution-phase-26)) |
| k3s did not start after a WSL restart | `systemctl status k3s`; confirm `/etc/wsl.conf` still has `systemd=true` |
| Need container-level detail | `sudo k3s crictl ps`, `sudo k3s crictl logs <id>` |
| Cluster feels slow | `kubectl top pods -A`, `free -h`, `swapon --show`; stop k3s if not in use |

General approach: inspect (`kubectl get`, `describe`, `logs`, `events`), find the actual error, make the smallest change, validate again. Do not reinstall or reset as a first step.

## 10. Uninstall procedure

**Documented only. Not executed.** This deletes the cluster and all its data.

```bash
sudo /usr/local/bin/k3s-uninstall.sh
```

This stops k3s and all containers, removes `k3s.service`, `/usr/local/bin/k3s` and its helper scripts, `/etc/rancher/k3s`, `/var/lib/rancher/k3s` and `/var/lib/kubelet`, and cleans up the CNI interfaces (`cni0`, `flannel.1`) and k3s iptables rules. Then remove the user-side pieces:

```bash
rm -f ~/.local/bin/kubectl     # the wrapper script (it would fail once k3s is gone)
rm -f ~/.kube/config           # only if nothing else uses it
```

Verify afterwards:

```bash
systemctl status k3s --no-pager        # should report the unit could not be found
ss -ltn | grep -E ':(6443|10250)\b'    # should print nothing
ss -ltn | grep -E ':(80|443)\b'        # should show only nginx
```

Not affected by uninstall: nginx, Backstage, PostgreSQL, OpenObserve, OPA, the OTel collector, the GitHub runners, Docker Desktop's dangling symlinks, `/tmp/k3s-install.sh`.

## Open items

1. **Windows `localhost` access to Traefik.** Traefik is reachable from Windows only through the WSL IP (or a port-forward), not `localhost:8880` ([section 7](#resolution-phase-26)). Revisit only if Windows-browser access by hostname is wanted (for example an nginx proxy layer, Option B in [ADR-001](../decisions/ADR-001-traefik-alternate-host-ports.md)).
2. **Memory headroom.** About 2.5 GiB available with swap in use ([section 8](#8-resource-impact)). Re-measure after Argo CD is added.

Resolved during Phase 1:

* `kubectl` works for the normal user through the wrapper ([section 4](#4-kubectl-and-kubeconfig)).
* Embedded containerd verified directly with `crictl` ([section 6](#6-validation-results-2026-09-20)).
