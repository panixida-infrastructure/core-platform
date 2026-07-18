#!/usr/bin/env bash
set -euo pipefail

timeweb_api="${TIMEWEB_API:-https://api.timeweb.cloud}"
openbao_addr="${OPENBAO_ADDR:-https://secrets.panixida.ru}"
openbao_role="${OPENBAO_ROLE:-core-platform-github-actions}"
openbao_audience="${OPENBAO_AUDIENCE:-https://github.com/panixida-infrastructure/core-platform}"

mail_domain="${TACTICAL_HEROES_MAIL_DOMAIN:-panixida.ru}"
mailbox="${TACTICAL_HEROES_MAILBOX:-tactical-heroes}"
mail_address="${mailbox}@${mail_domain}"

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "::error::${name} is required"
    exit 1
  fi
}

twc() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local response status

  response="$(mktemp)"

  if [ -n "$body" ]; then
    status="$(curl -sS \
      -o "$response" \
      -w '%{http_code}' \
      -X "$method" \
      -H "Authorization: Bearer ${TIMEWEB_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "$body" \
      "${timeweb_api}${path}")"
  else
    status="$(curl -sS \
      -o "$response" \
      -w '%{http_code}' \
      -X "$method" \
      -H "Authorization: Bearer ${TIMEWEB_TOKEN}" \
      -H "Content-Type: application/json" \
      "${timeweb_api}${path}")"
  fi

  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    jq '{status_code, error_code, message, response_id}' "$response" >&2 || true
    rm -f "$response"
    return 1
  fi

  cat "$response"
  rm -f "$response"
}

openbao_login() {
  require_env ACTIONS_ID_TOKEN_REQUEST_TOKEN
  require_env ACTIONS_ID_TOKEN_REQUEST_URL

  local encoded_audience oidc_response jwt login_payload
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

bao_read() {
  local token="$1"
  local path="$2"

  curl -fsS \
    -H "X-Vault-Token: ${token}" \
    "${openbao_addr}/v1/secret/data/${path}" | jq '.data.data'
}

bao_read_optional() {
  local token="$1"
  local path="$2"
  local response status

  response="$(mktemp)"
  status="$(curl -sS \
    -o "$response" \
    -w '%{http_code}' \
    -H "X-Vault-Token: ${token}" \
    "${openbao_addr}/v1/secret/data/${path}")"

  if [ "$status" = "404" ]; then
    rm -f "$response"
    echo '{}'
    return
  fi

  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    rm -f "$response"
    echo "::error::Failed to read OpenBao path ${path}: HTTP ${status}" >&2
    return 1
  fi

  jq '.data.data // {}' "$response"
  rm -f "$response"
}

bao_write() {
  local token="$1"
  local path="$2"
  local data="$3"

  curl -fsS \
    -X POST \
    -H "X-Vault-Token: ${token}" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --argjson data "$data" '{data: $data}')" \
    "${openbao_addr}/v1/secret/data/${path}" >/dev/null
}

mailbox_exists() {
  local status

  status="$(curl -sS \
    -o /dev/null \
    -w '%{http_code}' \
    -H "Authorization: Bearer ${TIMEWEB_TOKEN}" \
    "${timeweb_api}/api/v2/mail/domains/${mail_domain}/mailboxes/${mailbox}")"

  if [ "$status" = "200" ]; then
    return 0
  fi
  if [ "$status" = "404" ]; then
    return 1
  fi

  echo "::error::Failed to inspect mailbox ${mail_address}: HTTP ${status}" >&2
  exit 1
}

generate_password() {
  printf 'Smtp1!%s' "$(openssl rand -hex 16)"
}

require_env TIMEWEB_TOKEN
echo "Authenticating to OpenBao"
openbao_token="$(openbao_login)"

echo "Reading Tactical Heroes configuration from OpenBao"
applications_secret="$(bao_read_optional "$openbao_token" core-platform/applications)"
development_secret="$(bao_read "$openbao_token" applications/tactical-heroes-api/development)"
production_secret="$(bao_read "$openbao_token" applications/tactical-heroes-api/production)"

if [ "$(jq 'length' <<<"$development_secret")" -lt 10 ] || [ "$(jq 'length' <<<"$production_secret")" -lt 10 ]; then
  echo "::error::Tactical Heroes application configuration is missing in OpenBao"
  exit 1
fi

smtp_password="$(jq -r '.TACTICAL_HEROES_SMTP_PASSWORD // empty' <<<"$applications_secret")"
if [ -z "$smtp_password" ]; then
  smtp_password="$(generate_password)"
fi

mailbox_payload="$(jq -nc \
  --arg mailbox "$mailbox" \
  --arg password "$smtp_password" \
  '{
    mailbox: $mailbox,
    password: $password,
    comment: "Tactical Heroes application notifications",
    owner_full_name: "Tactical Heroes",
    filter_status: true,
    filter_action: "directory"
  }')"

echo "Inspecting mailbox ${mail_address}"
if mailbox_exists; then
  echo "Reconciling existing mailbox ${mail_address}"
  twc PATCH "/api/v2/mail/domains/${mail_domain}/mailboxes/${mailbox}" \
    "$(jq '{password, comment, owner_full_name}' <<<"$mailbox_payload")" >/dev/null
else
  echo "Creating mailbox ${mail_address}"
  twc POST "/api/v2/mail/domains/${mail_domain}" "$mailbox_payload" >/dev/null
fi

if ! mailbox_exists; then
  echo "::error::Mailbox ${mail_address} was not created"
  exit 1
fi

applications_secret="$(jq \
  --arg password "$smtp_password" \
  '. + {TACTICAL_HEROES_SMTP_PASSWORD: $password}' \
  <<<"$applications_secret")"

smtp_config="$(jq -nc \
  --arg address "$mail_address" \
  --arg password "$smtp_password" \
  '{
    Notifications__Email__Smtp__Host: "smtp.timeweb.ru",
    Notifications__Email__Smtp__Port: "587",
    Notifications__Email__Smtp__SocketOptions: "StartTls",
    Notifications__Email__Smtp__Username: $address,
    Notifications__Email__Smtp__Password: $password,
    Notifications__Email__Smtp__SenderEmail: $address
  }')"

development_secret="$(jq \
  --argjson smtp "$smtp_config" \
  '. + $smtp + {Notifications__Email__Smtp__SenderName: "Tactical Heroes Dev"}' \
  <<<"$development_secret")"
production_secret="$(jq \
  --argjson smtp "$smtp_config" \
  '. + $smtp + {Notifications__Email__Smtp__SenderName: "Tactical Heroes"}' \
  <<<"$production_secret")"

bao_write "$openbao_token" core-platform/applications "$applications_secret"
bao_write "$openbao_token" applications/tactical-heroes-api/development "$development_secret"
bao_write "$openbao_token" applications/tactical-heroes-api/production "$production_secret"

echo "Tactical Heroes mailbox and OpenBao SMTP configuration are reconciled"
