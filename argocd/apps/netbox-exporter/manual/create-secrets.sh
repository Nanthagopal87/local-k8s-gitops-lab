#!/usr/bin/env bash
# MANUAL STEP (ADR-009): run once before the first sync of the observability Applications:
#   bash argocd/apps/netbox-exporter/manual/create-secrets.sh
#
# Creates, in namespace monitoring:
#   grafana-admin          admin-user / admin-password (random) for Grafana. The chart would
#                          otherwise generate one at render time via `lookup`, which drifts
#                          under Argo CD (same trap as NetBox, ADR-008).
#   netbox-exporter-token  a copy of netbox/netbox-api-token, so the exporter can read the
#                          NetBox API (Secrets cannot be referenced across namespaces).
# Nothing is printed or written to disk. Idempotent: existing Secrets are left unchanged.
#
# Grafana login afterwards:
#   kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo
set -euo pipefail
NS=monitoring
rand() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }

kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"

label() { kubectl -n "$NS" label secret "$1" app.kubernetes.io/part-of=observability-learning-experiment learning-only=true >/dev/null; }

if kubectl -n "$NS" get secret grafana-admin >/dev/null 2>&1; then
  echo "secret/grafana-admin exists: left unchanged"
else
  kubectl -n "$NS" create secret generic grafana-admin \
    --from-literal=admin-user=admin --from-literal=admin-password="$(rand 24)"
  label grafana-admin
fi

if kubectl -n "$NS" get secret netbox-exporter-token >/dev/null 2>&1; then
  echo "secret/netbox-exporter-token exists: left unchanged"
else
  AUTH=$(kubectl -n netbox get secret netbox-api-token -o jsonpath='{.data.authorization}' | base64 -d)
  [ -n "$AUTH" ] || { echo "netbox/netbox-api-token missing: run argocd/apps/netbox/manual/create-api-token.sh first" >&2; exit 1; }
  kubectl -n "$NS" create secret generic netbox-exporter-token --from-literal=authorization="$AUTH"
  label netbox-exporter-token
fi
