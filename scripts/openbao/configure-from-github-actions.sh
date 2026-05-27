#!/usr/bin/env bash
set -euo pipefail

openbao_addr="${OPENBAO_ADDR:-https://secrets.panixida.ru}"
openbao_role="${OPENBAO_ROLE:-core-platform-github-actions}"
openbao_audience="${OPENBAO_AUDIENCE:-https://github.com/panixida-infrastructure/core-platform}"
openbao_namespace="${OPENBAO_K8S_NAMESPACE:-secrets}"
openbao_deployment="${OPENBAO_K8S_DEPLOYMENT:-deploy/openbao}"

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "::error::${name} is required"
    exit 1
  fi
}

openbao_login() {
  require_env ACTIONS_ID_TOKEN_REQUEST_TOKEN
  require_env ACTIONS_ID_TOKEN_REQUEST_URL

  local encoded_audience
  local oidc_response
  local jwt
  local login_payload

  encoded_audience="$(jq -rn --arg value "$openbao_audience" '$value|@uri')"
  oidc_response="$(curl -fsS \
    -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
    "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${encoded_audience}")"
  jwt="$(jq -r '.value' <<<"$oidc_response")"
  login_payload="$(jq -nc \
    --arg role "$openbao_role" \
    --arg jwt "$jwt" \
    '{role: $role, jwt: $jwt}')"

  curl -fsS \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$login_payload" \
    "${openbao_addr}/v1/auth/jwt/login" | jq -r '.auth.client_token'
}

openbao_token="$(openbao_login)"
openbao_oidc_client_secret="$(kubectl -n "$openbao_namespace" get secret openbao-secrets -o jsonpath='{.data.OPENBAO_OIDC_CLIENT_SECRET}' | base64 -d)"

script_b64="$(tr -d '\r' < scripts/openbao/configure.sh | base64 -w0)"
token_b64="$(printf '%s' "$openbao_token" | base64 -w0)"
client_secret_b64="$(printf '%s' "$openbao_oidc_client_secret" | base64 -w0)"

kubectl -n "$openbao_namespace" exec "$openbao_deployment" -- sh -c \
  "echo '$script_b64' | base64 -d > /tmp/configure-openbao.sh && \
   echo '$token_b64' | base64 -d > /tmp/openbao-token && \
   echo '$client_secret_b64' | base64 -d > /tmp/openbao-oidc-client-secret && \
   chmod 700 /tmp/configure-openbao.sh && \
   chmod 600 /tmp/openbao-token /tmp/openbao-oidc-client-secret"

kubectl -n "$openbao_namespace" exec "$openbao_deployment" -- sh -c '
  export BAO_ADDR=http://127.0.0.1:8200
  export VAULT_ADDR=$BAO_ADDR
  export BAO_TOKEN="$(cat /tmp/openbao-token)"
  export VAULT_TOKEN=$BAO_TOKEN
  export OPENBAO_OIDC_CLIENT_SECRET="$(cat /tmp/openbao-oidc-client-secret)"
  /bin/sh /tmp/configure-openbao.sh
  status=$?
  rm -f /tmp/configure-openbao.sh /tmp/openbao-token /tmp/openbao-oidc-client-secret
  exit "$status"
'
