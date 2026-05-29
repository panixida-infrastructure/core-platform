#!/usr/bin/env bash
set -euo pipefail

openbao_addr="${OPENBAO_ADDR:-https://secrets.panixida.ru}"
openbao_role="${OPENBAO_ROLE:-core-platform-github-actions}"
openbao_audience="${OPENBAO_AUDIENCE:-https://github.com/panixida-infrastructure/core-platform}"

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

bao_read() {
  local token="$1"
  local path="$2"

  curl -fsS \
    -H "X-Vault-Token: ${token}" \
    "${openbao_addr}/v1/secret/data/${path}" | jq '.data.data // {}'
}

apply_secret() {
  local namespace="$1"
  local name="$2"
  local data="$3"
  shift 3

  local keys_json
  local manifest

  keys_json="$(printf '%s\n' "$@" | jq -R . | jq -s .)"
  manifest="$(mktemp)"

  jq -n \
    --arg namespace "$namespace" \
    --arg name "$name" \
    --argjson data "$data" \
    --argjson keys "$keys_json" '
      def required($key):
        if (($data[$key] // "") | tostring | length) > 0 then
          $data[$key] | tostring
        else
          error("missing required secret key " + $key)
        end;
      {
        apiVersion: "v1",
        kind: "Secret",
        metadata: {
          name: $name,
          namespace: $namespace
        },
        type: "Opaque",
        stringData: (reduce $keys[] as $key ({}; .[$key] = required($key)))
      }' >"$manifest"

  kubectl apply --server-side --field-manager=core-platform-secrets-sync --force-conflicts -f "$manifest" >/dev/null
  kubectl -n "$namespace" annotate secret "$name" kubectl.kubernetes.io/last-applied-configuration- --overwrite >/dev/null 2>&1 || true
  rm -f "$manifest"
  echo "Synced Kubernetes secret ${namespace}/${name}"
}

apply_secret_json() {
  local namespace="$1"
  local name="$2"
  local data="$3"
  local manifest

  manifest="$(mktemp)"
  jq -n \
    --arg namespace "$namespace" \
    --arg name "$name" \
    --argjson data "$data" \
    '{
      apiVersion: "v1",
      kind: "Secret",
      metadata: {
        name: $name,
        namespace: $namespace
      },
      type: "Opaque",
      stringData: $data
    }' >"$manifest"

  kubectl apply --server-side --field-manager=core-platform-secrets-sync --force-conflicts -f "$manifest" >/dev/null
  kubectl -n "$namespace" annotate secret "$name" kubectl.kubernetes.io/last-applied-configuration- --overwrite >/dev/null 2>&1 || true
  rm -f "$manifest"
  echo "Synced Kubernetes secret ${namespace}/${name}"
}

apply_argocd_repository_secret() {
  local data="$1"
  local manifest

  manifest="$(mktemp)"
  jq -n \
    --argjson data "$data" \
    '
      def required($key):
        if (($data[$key] // "") | tostring | length) > 0 then
          $data[$key] | tostring
        else
          error("missing required secret key " + $key)
        end;
      {
        apiVersion: "v1",
        kind: "Secret",
        metadata: {
          name: "core-platform-repo",
          namespace: "argocd",
          labels: {
            "argocd.argoproj.io/secret-type": "repository"
          }
        },
        type: "Opaque",
        stringData: {
          type: "git",
          url: "https://github.com/panixida-infrastructure/core-platform.git",
          username: "x-access-token",
          password: required("SERVER_GH_PAT")
        }
      }' >"$manifest"

  kubectl apply --server-side --field-manager=core-platform-secrets-sync --force-conflicts -f "$manifest" >/dev/null
  kubectl -n argocd annotate secret core-platform-repo kubectl.kubernetes.io/last-applied-configuration- --overwrite >/dev/null 2>&1 || true
  rm -f "$manifest"
  echo "Synced Kubernetes secret argocd/core-platform-repo"
}

for namespace in argocd identity secrets observability quality headlamp; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done

openbao_token="$(openbao_login)"

github_secret="$(bao_read "$openbao_token" core-platform/github)"
identity_secret="$(bao_read "$openbao_token" core-platform/identity)"
openbao_secret="$(bao_read "$openbao_token" core-platform/openbao)"
observability_secret="$(bao_read "$openbao_token" core-platform/observability)"
sonarqube_secret="$(bao_read "$openbao_token" core-platform/sonarqube)"
sso_secret="$(bao_read "$openbao_token" core-platform/sso)"

apply_argocd_repository_secret "$github_secret"

apply_secret identity keycloak-secrets "$identity_secret" \
  KEYCLOAK_DB_HOST \
  KEYCLOAK_DB_PORT \
  KEYCLOAK_DB_NAME \
  KEYCLOAK_DB_USERNAME \
  KEYCLOAK_DB_PASSWORD \
  KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME \
  KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD

apply_secret secrets openbao-secrets "$openbao_secret" \
  OPENBAO_DB_HOST \
  OPENBAO_DB_PORT \
  OPENBAO_DB_NAME \
  OPENBAO_DB_USERNAME \
  OPENBAO_DB_PASSWORD \
  OPENBAO_OIDC_CLIENT_SECRET

apply_secret observability grafana-secrets "$observability_secret" \
  GRAFANA_DB_HOST \
  GRAFANA_DB_PORT \
  GRAFANA_DB_NAME \
  GRAFANA_DB_USERNAME \
  GRAFANA_DB_PASSWORD \
  GRAFANA_ADMIN_USER \
  GRAFANA_ADMIN_PASSWORD \
  GRAFANA_OIDC_CLIENT_SECRET \
  OBSERVABILITY_VM_REMOTE_WRITE_USERNAME \
  OBSERVABILITY_VM_REMOTE_WRITE_PASSWORD

apply_secret observability observability-secrets "$observability_secret" \
  OBSERVABILITY_VM_REMOTE_WRITE_USERNAME \
  OBSERVABILITY_VM_REMOTE_WRITE_PASSWORD \
  OBSERVABILITY_TELEGRAM_BOT_TOKEN \
  OBSERVABILITY_WIREGUARD_CONF

apply_secret quality sonarqube-secrets "$sonarqube_secret" \
  SONAR_DB_HOST \
  SONAR_DB_PORT \
  SONAR_DB_NAME \
  SONAR_DB_USERNAME \
  SONAR_DB_PASSWORD \
  SONAR_ADMIN_PASSWORD

headlamp_oidc_secret="$(jq -n \
  --argjson sso "$sso_secret" \
  '
    def required($key):
      if (($sso[$key] // "") | tostring | length) > 0 then
        $sso[$key] | tostring
      else
        error("missing required secret key " + $key)
      end;
    {
      OIDC_CLIENT_ID: "kubernetes",
      OIDC_CLIENT_SECRET: required("HEADLAMP_OIDC_CLIENT_SECRET"),
      OIDC_ISSUER_URL: "https://identity.panixida.ru/realms/panixida",
      OIDC_SCOPES: "profile email"
    }')"
apply_secret_json headlamp headlamp-oidc "$headlamp_oidc_secret"

keycloak_sso_client_secret="$(jq -n \
  --argjson openbao "$openbao_secret" \
  --argjson observability "$observability_secret" \
  --argjson sso "$sso_secret" \
  '
    def required($source; $key):
      if (($source[$key] // "") | tostring | length) > 0 then
        $source[$key] | tostring
      else
        error("missing required secret key " + $key)
      end;
    {
      GRAFANA_OIDC_CLIENT_SECRET: required($observability; "GRAFANA_OIDC_CLIENT_SECRET"),
      OPENBAO_OIDC_CLIENT_SECRET: required($openbao; "OPENBAO_OIDC_CLIENT_SECRET"),
      KUBERNETES_OIDC_CLIENT_SECRET: required($sso; "HEADLAMP_OIDC_CLIENT_SECRET")
    }')"
apply_secret_json identity keycloak-sso-client-secrets "$keycloak_sso_client_secret"
