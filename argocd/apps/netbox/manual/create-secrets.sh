#!/usr/bin/env bash
# MANUAL STEP: NOT applied by Argo CD. Run once, before the first sync of the netbox
# Application:   bash argocd/apps/netbox/manual/create-secrets.sh
#
# Creates the three Secrets NetBox needs, with random values generated locally. Nothing is
# written to disk or to Git; that keeps the ADR-003 boundary (no Secret is ever managed by
# Argo CD) without even committing fake values.
#
# Idempotent: an existing Secret is left untouched. That matters: PostgreSQL only reads its
# password when it first initialises the volume, so regenerating netbox-db later would lock
# NetBox out of its own database.
#
# Read the admin password afterwards with:
#   kubectl -n netbox get secret netbox-superuser -o jsonpath='{.data.password}' | base64 -d; echo
set -euo pipefail

NS=netbox
rand() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }

kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"

create() { # name, then --from-literal args
  local name=$1; shift
  if kubectl -n "$NS" get secret "$name" >/dev/null 2>&1; then
    echo "secret/$name exists: left unchanged"
  else
    kubectl -n "$NS" create secret generic "$name" "$@"
    kubectl -n "$NS" label secret "$name" app.kubernetes.io/part-of=netbox-learning-experiment learning-only=true >/dev/null
  fi
}

# Django secret key (50+ chars) and the API-token pepper map (NetBox v2 tokens).
create netbox-config \
  --from-literal=secret_key="$(rand 60)" \
  --from-literal=api_token_peppers="{\"1\": \"$(rand 60)\"}" \
  --from-literal=email_password=""

# Initial admin user, created by the NetBox container on first start.
create netbox-superuser \
  --from-literal=username=admin \
  --from-literal=email=admin@example.com \
  --from-literal=password="$(rand 24)" \
  --from-literal=api_token="$(LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 40)"

# Database password, shared by data/postgres.yaml and NetBox.
create netbox-db \
  --from-literal=postgresql-password="$(rand 32)"
