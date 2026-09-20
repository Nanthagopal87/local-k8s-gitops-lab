# kubernetes/learning: manual exceptions (NOT managed by Argo CD)

Since Phase 4 the adopted Phase 2 resources live in [`argocd/apps/k8s-learning/`](../../argocd/apps/k8s-learning/)
and are managed by the Argo CD Application `k8s-learning`. What remains here is deliberately **outside** Argo CD:

| File | Why it is not managed by Argo CD |
|---|---|
| `secret/demo-web-secret.yaml` | An obviously **fake** learning Secret. Secrets stay outside Git-driven sync until a real secrets-management approach exists. base64 is encoding, not encryption, and real credentials must never be committed. |
| `pod/pod.yaml` | A disposable demo Pod, not a long-running workload. |
| `service/demo-web-nodeport.yaml` | A demo NodePort Service that is removed after use. |
| `storage/pvc-writer-pod.yaml` | A disposable Pod used only to demonstrate the PVC. |

Apply these by hand only when you want the demo (`kubectl apply -f <file>`), and delete them afterwards.
The live fake Secret `demo-web-secret` is referenced by the Argo CD-managed Deployment but is not owned by Argo CD.
See [docs/argocd/gitops-adoption.md](../../docs/argocd/gitops-adoption.md).
