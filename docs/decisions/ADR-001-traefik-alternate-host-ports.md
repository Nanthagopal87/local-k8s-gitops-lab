# ADR-001: Expose Traefik on alternate host ports (8880/8843) so nginx keeps 80/443

## Status

Accepted and implemented on 2026-09-20 (Phase 2.6).

## Context

The WSL2 Ubuntu host already runs **nginx** (outside Kubernetes) on `0.0.0.0:80` and `0.0.0.0:443`. It redirects HTTP to HTTPS and reverse-proxies `https://localhost/` to Backstage (`localhost:7007`) and `/keycloak` to `localhost:8080`.

The k3s-bundled **Traefik** is published through the default **ServiceLB** (klipper-lb). For a `LoadBalancer` Service, ServiceLB runs a DaemonSet whose Pods request `hostPort` equal to the Service port. Traefik's Service used ports 80 and 443, so the `svclb-traefik` Pod requested `hostPort` 80 and 443.

Inspection in Phase 1 and Phase 2.6 showed:

* Traefik itself holds no host ports (no `hostNetwork`, no `hostPort`; it listens on container ports 8000/8443).
* `hostPort` is implemented as netfilter NAT rules, not a listening socket. So there was no bind error, ServiceLB reported success, and `ss` still showed nginx listening on 80/443.
* Requests to `http(s)://localhost` were nevertheless answered by Traefik (a 404 page and `TRAEFIK DEFAULT CERT`), never by nginx. A marked request produced 0 lines in nginx's access log. This also applied to Windows-side `localhost`, so the existing Backstage route via nginx was effectively down while k3s ran.
* Verified with root (`iptables-save -t nat`, captured before the fix): `PREROUTING` and `OUTPUT` jump (for local destinations) to `CNI-HOSTPORT-DNAT`, which DNATs `--dport 80` and `443` to the svclb pod (`10.42.0.7:80` / `:443`), including for `127.0.0.1` sources. The same capture showed nginx's worker processes holding the listening sockets on 80/443.

Constraints: keep nginx and Backstage untouched, keep the default k3s Traefik and ServiceLB enabled, do not change WSL networking/firewall, keep the change small and reversible, and stay compatible with a later Argo CD install.

WSL2 detail that shaped the decision: Windows `localhost` forwarding only carries ports that have a real listening socket. NAT-based ports (NodePort, `hostPort`) work from inside WSL via `localhost` and from Windows via the WSL IP, but not via Windows `localhost`.

## Decision

Keep Traefik as a `LoadBalancer` Service behind ServiceLB, but move its **external** ports to **8880 (HTTP)** and **8843 (HTTPS)** using a k3s `HelmChartConfig`:

```yaml
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata: {name: traefik, namespace: kube-system}
spec:
  valuesContent: |-
    ports:
      web:       {exposedPort: 8880}
      websecure: {exposedPort: 8843}
```

Stored in the repo at `kubernetes/platform/traefik/helmchartconfig.yaml` and applied with `kubectl apply`. Ports 8080/8443 were deliberately avoided (nginx's Keycloak upstream is `localhost:8080`). Traefik's container ports, its Deployment and all Ingress objects are unchanged.

No nginx configuration, no k3s configuration file (`/etc/rancher/k3s`), no iptables/nftables and no WSL settings were modified.

## Alternatives Considered

| Option | Verdict |
|---|---|
| **A2.** `service.spec.type: NodePort` with fixed nodePorts | Viable. Cannot hijack host ports because the NodePort range is reserved. But a NodePort Service has no LoadBalancer status, so Traefik would likely publish no address on Ingress objects, which Argo CD's built-in health rules would show as `Progressing` (reasoned, not exercised). Rejected for now; the fallback if host-port hijacking becomes a problem. |
| **B.** nginx reverse-proxies selected hostnames to Traefik | Does not fix the problem alone (ServiceLB would still intercept 80/443), and it modifies nginx. May be layered on later if Windows-browser access by hostname is wanted. |
| **C.** Disable ServiceLB (`config.yaml` plus a k3s restart) | Touches a root-owned k3s file, restarts the control plane, and leaves the Service `<pending>` with no Ingress address. Rejected. |
| **D.** Disable Traefik | Loses the Ingress learning goal. Rejected. |
| **E.** Stop k3s when nginx is needed | Makes both unavailable half the time. Rejected. |

## Consequences

Positive:

* nginx and Backstage own 80/443 again. Verified: `http://localhost/` returns nginx's `301`, `:443` presents `CN=localhost`, marked requests appear in nginx's access log, and Windows `curl.exe` sees nginx.
* Traefik still routes the learning Ingress on `8880`/`8843` (verified with `curl --resolve`), and the NodePorts `30819`/`31320` are unchanged.
* The Ingress `ADDRESS` (`<WSL-IP>`) is preserved, which should keep Argo CD Ingress health working.
* One small CR, applied in about 12 seconds without restarting Traefik's Pod, fully reversible.

Negative / to remember:

* The Traefik **Service** ports changed (80/443 to 8880/8843). Anything addressing the Service by port 80/443 must change (for example the Phase 2 test through Traefik's ClusterIP now uses `:8880`).
* Reaching Traefik from Windows needs the WSL IP (`http://<WSL-IP>:8880`; the IP can change on WSL restart). Windows `localhost:8880` does not work (verified). A real listener, such as `kubectl port-forward`, is needed for that.
* The same class of problem remains: ServiceLB `hostPort`s can intercept any host application later bound to 8880/8843. Choose those ports carefully; A2 removes this risk.
* The `HelmChartConfig` lives in k3s's datastore. Uninstalling k3s removes it; the repo copy remains.

Rollback: `kubectl delete -f kubernetes/platform/traefik/helmchartconfig.yaml`. The Helm controller returns Traefik to ports 80/443, which shadows nginx again.
