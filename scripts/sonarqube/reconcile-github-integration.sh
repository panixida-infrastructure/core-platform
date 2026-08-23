#!/usr/bin/env bash
set -euo pipefail

openbao_addr="${OPENBAO_ADDR:-https://secrets.panixida.ru}"
openbao_role="${OPENBAO_ROLE:-core-platform-github-actions}"
openbao_audience="${OPENBAO_AUDIENCE:-https://github.com/panixida-infrastructure/core-platform}"
sonar_url="${SONAR_URL:-https://sonar.panixida.ru}"
github_integration_key="${SONAR_GITHUB_INTEGRATION_KEY:-GitHub}"
github_api_url="${SONAR_GITHUB_API_URL:-https://api.github.com}"

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "::error::${name} is required"
    exit 1
  fi
}

require_secret_key() {
  local secret="$1"
  local key="$2"
  if ! jq -e --arg key "$key" '(.[$key] // "") | strings | length > 0' <<<"$secret" >/dev/null; then
    echo "::error::OpenBao secret core-platform/sonarqube is missing ${key}"
    exit 1
  fi
}

require_env ACTIONS_ID_TOKEN_REQUEST_TOKEN
require_env ACTIONS_ID_TOKEN_REQUEST_URL

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

sonar_secret="$(curl -fsS \
  -H "X-Vault-Token: ${openbao_token}" \
  "${openbao_addr}/v1/secret/data/core-platform/sonarqube" | jq '.data.data // {}')"

for key in \
  SONAR_ADMIN_PASSWORD \
  SONAR_GITHUB_APP_ID \
  SONAR_GITHUB_CLIENT_ID \
  SONAR_GITHUB_CLIENT_SECRET \
  SONAR_GITHUB_PRIVATE_KEY; do
  require_secret_key "$sonar_secret" "$key"
done

sonar_admin_password="$(jq -r '.SONAR_ADMIN_PASSWORD' <<<"$sonar_secret")"
github_app_id="$(jq -r '.SONAR_GITHUB_APP_ID' <<<"$sonar_secret")"
github_client_id="$(jq -r '.SONAR_GITHUB_CLIENT_ID' <<<"$sonar_secret")"
github_client_secret="$(jq -r '.SONAR_GITHUB_CLIENT_SECRET' <<<"$sonar_secret")"
github_private_key="$(jq -r '.SONAR_GITHUB_PRIVATE_KEY' <<<"$sonar_secret")"
auth="admin:${sonar_admin_password}"

for _ in $(seq 1 60); do
  if curl -fsS "${sonar_url}/api/system/status" 2>/dev/null | grep -q '"UP"'; then
    break
  fi
  sleep 5
done

if ! curl -fsS "${sonar_url}/api/system/status" 2>/dev/null | grep -q '"UP"'; then
  echo "::error::SonarQube did not become UP before configuration timeout"
  exit 1
fi

if ! curl -fsS -u "$auth" "${sonar_url}/api/authentication/validate" \
  | grep -Eq '"valid"[[:space:]]*:[[:space:]]*true'; then
  echo "::error::SonarQube admin credentials are not valid"
  exit 1
fi

definitions="$(curl -fsS -u "$auth" "${sonar_url}/api/alm_settings/list_definitions")"
if printf '%s' "$definitions" | tr -d '\n\r ' | grep -Fq "\"key\":\"${github_integration_key}\""; then
  endpoint="update_github"
  key_arguments=(--data-urlencode "key=${github_integration_key}" --data-urlencode "newKey=${github_integration_key}")
else
  endpoint="create_github"
  key_arguments=(--data-urlencode "key=${github_integration_key}")
fi

curl -fsS -u "$auth" -X POST "${sonar_url}/api/alm_settings/${endpoint}" \
  "${key_arguments[@]}" \
  --data-urlencode "url=${github_api_url}" \
  --data-urlencode "appId=${github_app_id}" \
  --data-urlencode "clientId=${github_client_id}" \
  --data-urlencode "clientSecret=${github_client_secret}" \
  --data-urlencode "privateKey=${github_private_key}" >/dev/null

curl -fsS -u "$auth" --get "${sonar_url}/api/alm_settings/validate" \
  --data-urlencode "key=${github_integration_key}" >/dev/null

echo "SonarQube GitHub integration reconciled and validated"
