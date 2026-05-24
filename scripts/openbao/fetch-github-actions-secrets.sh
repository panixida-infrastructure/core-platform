#!/usr/bin/env bash
set -euo pipefail

openbao_addr="${OPENBAO_ADDR:-https://secrets.panixida.ru}"
openbao_role="${OPENBAO_ROLE:-core-platform-github-actions}"
openbao_audience="${OPENBAO_AUDIENCE:-https://github.com/PANiXiDA-Infrastructure/core-platform}"

if [ -z "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" ] || [ -z "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]; then
  echo "GitHub Actions OIDC request variables are not available" >&2
  exit 1
fi

encoded_audience="$(jq -rn --arg value "$openbao_audience" '$value|@uri')"
oidc_response="$(curl -fsS \
  -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${encoded_audience}")"
jwt="$(jq -r '.value' <<<"$oidc_response")"

login_payload="$(jq -nc \
  --arg role "$openbao_role" \
  --arg jwt "$jwt" \
  '{role: $role, jwt: $jwt}')"

openbao_token="$(curl -fsS \
  -X POST \
  -H "Content-Type: application/json" \
  -d "$login_payload" \
  "${openbao_addr}/v1/auth/jwt/login" | jq -r '.auth.client_token')"

emit_secret() {
  local output_name="$1"
  local secret_path="$2"
  local secret_key="$3"
  local value

  value="$(curl -fsS \
    -H "X-Vault-Token: ${openbao_token}" \
    "${openbao_addr}/v1/secret/data/${secret_path}" | jq -r --arg key "$secret_key" '.data.data[$key]')"

  if [ -z "$value" ] || [ "$value" = "null" ]; then
    echo "Secret ${secret_path}.${secret_key} is empty or missing" >&2
    exit 1
  fi

  echo "::add-mask::${value}"
  {
    printf '%s<<EOF\n' "$output_name"
    printf '%s\n' "$value"
    printf 'EOF\n'
  } >> "$GITHUB_OUTPUT"
}

case "${1:-}" in
  bootstrap)
    emit_secret SERVER_GH_PAT core-platform/github SERVER_GH_PAT
    emit_secret WIREGUARD_PRIVATE_KEY core-platform/wireguard WIREGUARD_PRIVATE_KEY
    emit_secret WIREGUARD_PRESHARED_KEY core-platform/wireguard WIREGUARD_PRESHARED_KEY
    emit_secret TACTICALHEROES_DEV_SSH_PRIVATE_KEY core-platform/ssh/tacticalheroes-dev TACTICALHEROES_DEV_SSH_PRIVATE_KEY
    emit_secret OBSERVABILITY_VM_REMOTE_WRITE_USERNAME core-platform/observability OBSERVABILITY_VM_REMOTE_WRITE_USERNAME
    emit_secret OBSERVABILITY_VM_REMOTE_WRITE_PASSWORD core-platform/observability OBSERVABILITY_VM_REMOTE_WRITE_PASSWORD
    ;;
  edge)
    emit_secret OAUTH2_PROXY_CLIENT_SECRET core-platform/sso OAUTH2_PROXY_CLIENT_SECRET
    emit_secret OAUTH2_PROXY_COOKIE_SECRET core-platform/sso OAUTH2_PROXY_COOKIE_SECRET
    ;;
  identity)
    emit_secret KEYCLOAK_DB_USERNAME core-platform/identity KEYCLOAK_DB_USERNAME
    emit_secret KEYCLOAK_DB_PASSWORD core-platform/identity KEYCLOAK_DB_PASSWORD
    emit_secret KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME core-platform/identity KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME
    emit_secret KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD core-platform/identity KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD
    ;;
  identity-sso)
    emit_secret KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME core-platform/identity KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME
    emit_secret KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD core-platform/identity KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD
    emit_secret KOMODO_OIDC_CLIENT_SECRET core-platform/komodo KOMODO_OIDC_CLIENT_SECRET
    emit_secret OAUTH2_PROXY_CLIENT_SECRET core-platform/sso OAUTH2_PROXY_CLIENT_SECRET
    emit_secret GRAFANA_OIDC_CLIENT_SECRET core-platform/observability GRAFANA_OIDC_CLIENT_SECRET
    emit_secret OPENBAO_OIDC_CLIENT_SECRET core-platform/openbao OPENBAO_OIDC_CLIENT_SECRET
    ;;
  komodo)
    emit_secret KOMODO_DATABASE_USERNAME core-platform/komodo KOMODO_DATABASE_USERNAME
    emit_secret KOMODO_DATABASE_PASSWORD core-platform/komodo KOMODO_DATABASE_PASSWORD
    emit_secret KOMODO_INIT_ADMIN_USERNAME core-platform/komodo KOMODO_INIT_ADMIN_USERNAME
    emit_secret KOMODO_INIT_ADMIN_PASSWORD core-platform/komodo KOMODO_INIT_ADMIN_PASSWORD
    emit_secret KOMODO_WEBHOOK_SECRET core-platform/komodo KOMODO_WEBHOOK_SECRET
    emit_secret KOMODO_JWT_SECRET core-platform/komodo KOMODO_JWT_SECRET
    emit_secret KOMODO_OIDC_CLIENT_SECRET core-platform/komodo KOMODO_OIDC_CLIENT_SECRET
    ;;
  observability)
    emit_secret GRAFANA_ADMIN_USER core-platform/observability GRAFANA_ADMIN_USER
    emit_secret GRAFANA_ADMIN_PASSWORD core-platform/observability GRAFANA_ADMIN_PASSWORD
    emit_secret GRAFANA_OIDC_CLIENT_SECRET core-platform/observability GRAFANA_OIDC_CLIENT_SECRET
    emit_secret OBSERVABILITY_VM_REMOTE_WRITE_USERNAME core-platform/observability OBSERVABILITY_VM_REMOTE_WRITE_USERNAME
    emit_secret OBSERVABILITY_VM_REMOTE_WRITE_PASSWORD core-platform/observability OBSERVABILITY_VM_REMOTE_WRITE_PASSWORD
    ;;
  sonarqube)
    emit_secret SONAR_DB_USERNAME core-platform/sonarqube SONAR_DB_USERNAME
    emit_secret SONAR_DB_PASSWORD core-platform/sonarqube SONAR_DB_PASSWORD
    emit_secret SONAR_ADMIN_PASSWORD core-platform/sonarqube SONAR_ADMIN_PASSWORD
    ;;
  *)
    echo "Usage: $0 {bootstrap|edge|identity|identity-sso|komodo|observability|sonarqube}" >&2
    exit 2
    ;;
esac
