#!/usr/bin/env bash
# MANUAL STEP: run once, AFTER the netbox Application is Synced/Healthy:
#   bash argocd/apps/netbox/manual/create-api-token.sh
#
# Why this exists: NetBox v4.7's image only creates the admin's API token when it receives
# both SUPERUSER_API_TOKEN and SUPERUSER_API_KEY (v2 tokens: "Bearer nbt_<key>.<token>").
# Chart 8.3.101 passes only the token, so no token is created (found 2026-11-25; the
# api_token key in netbox-superuser is therefore unused). This script asks NetBox itself to
# generate a v2 token and stores the full Authorization header in the netbox-api-token
# Secret. The value is never printed or written to disk. Idempotent.
#
# Use it with:
#   A=$(kubectl -n netbox get secret netbox-api-token -o jsonpath='{.data.authorization}' | base64 -d)
#   curl -H "Authorization: $A" http://127.0.0.1:8001/api/status/
set -euo pipefail
NS=netbox

if kubectl -n "$NS" get secret netbox-api-token >/dev/null 2>&1; then
  echo "secret/netbox-api-token exists: left unchanged"; exit 0
fi

HEADER=$(kubectl -n "$NS" exec deploy/netbox -c netbox -- sh -c 'cd /opt/netbox/netbox && ./manage.py shell --no-startup --no-imports --interface python -c "
from users.models import Token, User
from users.choices import TokenVersionChoices
t = Token(user=User.objects.get(username=\"admin\"), version=TokenVersionChoices.V2,
          description=\"lab automation (manual/create-api-token.sh, ADR-008)\")
t.save()
print(t.get_auth_header_prefix() + t.token)
"' 2>/dev/null | tail -1)

case "$HEADER" in
  "Bearer nbt_"*) ;;
  *) echo "unexpected output from NetBox; nothing stored" >&2; exit 1 ;;
esac

kubectl -n "$NS" create secret generic netbox-api-token --from-literal=authorization="$HEADER"
kubectl -n "$NS" label secret netbox-api-token app.kubernetes.io/part-of=netbox-learning-experiment learning-only=true >/dev/null
