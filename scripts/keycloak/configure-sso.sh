#!/usr/bin/env bash
set -euo pipefail

kc=/opt/keycloak/bin/kcadm.sh
realm="${KEYCLOAK_REALM:-panixida}"
server="${KEYCLOAK_INTERNAL_URL:-http://127.0.0.1:8080}"

required_vars=(
  KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME
  KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD
  SSO_ADMIN_USERNAME
  SSO_ADMIN_PASSWORD
  KOMODO_OIDC_CLIENT_SECRET
  OAUTH2_PROXY_CLIENT_SECRET
  GRAFANA_OIDC_CLIENT_SECRET
  OPENBAO_OIDC_CLIENT_SECRET
)

for name in "${required_vars[@]}"; do
  if [ -z "${!name:-}" ]; then
    echo "${name} is required" >&2
    exit 1
  fi
done

"$kc" config credentials \
  --server "$server" \
  --realm master \
  --user "$KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME" \
  --password "$KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null

if ! "$kc" get "realms/${realm}" >/dev/null 2>&1; then
  "$kc" create realms \
    -s "realm=${realm}" \
    -s enabled=true \
    -s displayName=Panixida \
    -s sslRequired=external
fi

ensure_user() {
  local username="$1"
  local password="$2"
  local email="${3:-admin@panixida.ru}"
  local user_id

  user_id="$("$kc" get users -r "$realm" -q "username=${username}" --fields id --format csv --noquotes | tr -d '\r' | tail -n 1)"
  if [ -z "$user_id" ]; then
    "$kc" create users -r "$realm" \
      -s "username=${username}" \
      -s enabled=true \
      -s emailVerified=true \
      -s "email=${email}" >/dev/null
    user_id="$("$kc" get users -r "$realm" -q "username=${username}" --fields id --format csv --noquotes | tr -d '\r' | tail -n 1)"
  fi

  "$kc" set-password -r "$realm" \
    --userid "$user_id" \
    --new-password "$password" \
    --temporary=false
}

ensure_oidc_client() {
  local client_id="$1"
  local secret="$2"
  local redirect_uris="$3"
  local web_origins="$4"
  local id

  id="$("$kc" get clients -r "$realm" -q "clientId=${client_id}" --fields id --format csv --noquotes | tr -d '\r' | tail -n 1)"
  if [ -z "$id" ]; then
    "$kc" create clients -r "$realm" \
      -s "clientId=${client_id}" \
      -s protocol=openid-connect \
      -s enabled=true >/dev/null
    id="$("$kc" get clients -r "$realm" -q "clientId=${client_id}" --fields id --format csv --noquotes | tr -d '\r' | tail -n 1)"
  fi

  "$kc" update "clients/${id}" -r "$realm" \
    -s enabled=true \
    -s protocol=openid-connect \
    -s publicClient=false \
    -s standardFlowEnabled=true \
    -s implicitFlowEnabled=false \
    -s directAccessGrantsEnabled=false \
    -s serviceAccountsEnabled=false \
    -s "secret=${secret}" \
    -s "redirectUris=${redirect_uris}" \
    -s "webOrigins=${web_origins}"
}

ensure_user "$SSO_ADMIN_USERNAME" "$SSO_ADMIN_PASSWORD" "${SSO_ADMIN_EMAIL:-admin@panixida.ru}"

ensure_oidc_client \
  komodo \
  "$KOMODO_OIDC_CLIENT_SECRET" \
  '["https://komodo.panixida.ru/auth/oidc/callback"]' \
  '["https://komodo.panixida.ru"]'

ensure_oidc_client \
  panixida-edge \
  "$OAUTH2_PROXY_CLIENT_SECRET" \
  '["https://auth.panixida.ru/oauth2/callback"]' \
  '["https://auth.panixida.ru"]'

ensure_oidc_client \
  grafana \
  "$GRAFANA_OIDC_CLIENT_SECRET" \
  '["https://grafana.panixida.ru/login/generic_oauth"]' \
  '["https://grafana.panixida.ru"]'

ensure_oidc_client \
  openbao \
  "$OPENBAO_OIDC_CLIENT_SECRET" \
  '["https://secrets.panixida.ru/ui/vault/auth/oidc/oidc/callback"]' \
  '["https://secrets.panixida.ru"]'
