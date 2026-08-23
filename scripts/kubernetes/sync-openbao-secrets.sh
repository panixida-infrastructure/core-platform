#!/usr/bin/env bash
set -euo pipefail

openbao_addr="${OPENBAO_ADDR:-https://secrets.panixida.ru}"
openbao_role="${OPENBAO_ROLE:-core-platform-github-actions}"
openbao_audience="${OPENBAO_AUDIENCE:-https://github.com/panixida-infrastructure/core-platform}"
rotate_observability_password="${ROTATE_OBSERVABILITY_PASSWORD:-false}"

if [ "$rotate_observability_password" != "true" ] && [ "$rotate_observability_password" != "false" ]; then
  echo "::error::ROTATE_OBSERVABILITY_PASSWORD must be true or false"
  exit 1
fi

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

bao_write() {
  local token="$1"
  local path="$2"
  local data="$3"
  local payload

  payload="$(jq -nc --argjson data "$data" '{data: $data}')"
  curl -fsS \
    -X POST \
    -H "X-Vault-Token: ${token}" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "${openbao_addr}/v1/secret/data/${path}" >/dev/null
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
      {
        apiVersion: "v1",
        kind: "Secret",
        metadata: {
          name: $name,
          namespace: $namespace
        },
        type: "Opaque",
        stringData: (reduce $keys[] as $entry ({};
          ($entry | startswith("?")) as $optional
          | (if $optional then $entry[1:] else $entry end) as $key
          | (($data[$key] // "") | tostring) as $value
          | if $optional and ($value | length) == 0 then
              .
            elif ($value | length) > 0 then
              .[$key] = $value
            else
              error("missing required secret key " + $key)
            end
        ))
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
  local name="$1"
  local repo_url="$2"
  local data="$3"
  local manifest

  manifest="$(mktemp)"
  jq -n \
    --arg name "$name" \
    --arg repo_url "$repo_url" \
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
          name: $name,
          namespace: "argocd",
          labels: {
            "argocd.argoproj.io/secret-type": "repository"
          }
        },
        type: "Opaque",
        stringData: {
          type: "git",
          url: $repo_url,
          username: "x-access-token",
          password: required("SERVER_GH_PAT")
        }
      }' >"$manifest"

  kubectl apply --server-side --field-manager=core-platform-secrets-sync --force-conflicts -f "$manifest" >/dev/null
  kubectl -n argocd annotate secret "$name" kubectl.kubernetes.io/last-applied-configuration- --overwrite >/dev/null 2>&1 || true
  rm -f "$manifest"
  echo "Synced Kubernetes secret argocd/${name}"
}

apply_kargo_repository_secret() {
  local namespace="$1"
  local name="$2"
  local cred_type="$3"
  local repo_url="$4"
  local username="$5"
  local password="$6"
  local manifest

  manifest="$(mktemp)"
  jq -n \
    --arg namespace "$namespace" \
    --arg name "$name" \
    --arg cred_type "$cred_type" \
    --arg repo_url "$repo_url" \
    --arg username "$username" \
    --arg password "$password" \
    '{
      apiVersion: "v1",
      kind: "Secret",
      metadata: {
        name: $name,
        namespace: $namespace,
        labels: {
          "kargo.akuity.io/cred-type": $cred_type
        }
      },
      type: "Opaque",
      stringData: {
        repoURL: $repo_url,
        username: $username,
        password: $password
      }
    }' >"$manifest"

  kubectl apply --server-side --field-manager=core-platform-secrets-sync --force-conflicts -f "$manifest" >/dev/null
  kubectl -n "$namespace" annotate secret "$name" kubectl.kubernetes.io/last-applied-configuration- --overwrite >/dev/null 2>&1 || true
  rm -f "$manifest"
  echo "Synced Kubernetes secret ${namespace}/${name}"
}

for namespace in argocd identity secrets observability quality headlamp; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done

kubectl apply -f - >/dev/null <<'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: dotnet-template
  labels:
    kargo.akuity.io/project: "true"
---
apiVersion: v1
kind: Namespace
metadata:
  name: tactical-heroes
  labels:
    kargo.akuity.io/project: "true"
---
apiVersion: v1
kind: Namespace
metadata:
  name: tactical-heroes-admin
  labels:
    kargo.akuity.io/project: "true"
EOF

openbao_token="$(openbao_login)"

github_secret="$(bao_read "$openbao_token" core-platform/github)"
identity_secret="$(bao_read "$openbao_token" core-platform/identity)"
openbao_secret="$(bao_read "$openbao_token" core-platform/openbao)"
observability_secret="$(bao_read "$openbao_token" core-platform/observability)"

if [ "$rotate_observability_password" = "true" ]; then
  observability_password="$(openssl rand -base64 32 | tr -d '\n')"
  observability_secret="$(jq --arg password "$observability_password" '.OBSERVABILITY_VM_REMOTE_WRITE_PASSWORD = $password' <<<"$observability_secret")"
  bao_write "$openbao_token" core-platform/observability "$observability_secret"
  unset observability_password
  echo "Rotated the observability basic-auth password in OpenBao"
fi

sonarqube_secret="$(bao_read "$openbao_token" core-platform/sonarqube)"

if [ -z "$(jq -r '.SONAR_AUTH_JWTBASE64HS256SECRET // empty' <<<"$sonarqube_secret")" ]; then
  sonar_auth_jwt_secret="$(openssl rand -base64 32 | tr -d '\n')"
  sonarqube_secret="$(jq \
    --arg secret "$sonar_auth_jwt_secret" \
    '.SONAR_AUTH_JWTBASE64HS256SECRET = $secret' \
    <<<"$sonarqube_secret")"
  bao_write "$openbao_token" core-platform/sonarqube "$sonarqube_secret"
  unset sonar_auth_jwt_secret
  echo "Generated the SonarQube session JWT secret in OpenBao"
fi

sso_secret="$(bao_read "$openbao_token" core-platform/sso)"
dotnet_template_registry_secret="$(bao_read "$openbao_token" applications/dotnet-template/registry)"
tactical_heroes_development_secret="$(bao_read "$openbao_token" applications/tactical-heroes-api/development)"
tactical_heroes_production_secret="$(bao_read "$openbao_token" applications/tactical-heroes-api/production)"
tactical_heroes_registry_secret="$(bao_read "$openbao_token" applications/tactical-heroes-api/registry)"

if [ "$(jq 'length' <<<"$tactical_heroes_development_secret")" -lt 10 ] || [ "$(jq 'length' <<<"$tactical_heroes_production_secret")" -lt 10 ]; then
  echo "::error::Tactical Heroes application configuration is missing in OpenBao"
  exit 1
fi

tactical_heroes_development_secret="$(jq \
  '.OAuthSpa__LoginUrl = "https://dev.tactical-heroes.panixida.ru/login"' \
  <<<"$tactical_heroes_development_secret")"
tactical_heroes_production_secret="$(jq \
  '.OAuthSpa__LoginUrl = "https://tactical-heroes.panixida.ru/login"' \
  <<<"$tactical_heroes_production_secret")"

bao_write "$openbao_token" applications/tactical-heroes-api/development "$tactical_heroes_development_secret"
bao_write "$openbao_token" applications/tactical-heroes-api/production "$tactical_heroes_production_secret"
bao_write "$openbao_token" applications/tactical-heroes-admin/registry "$tactical_heroes_registry_secret"

apply_argocd_repository_secret \
  core-platform-repo \
  https://github.com/panixida-infrastructure/core-platform.git \
  "$github_secret"

apply_argocd_repository_secret \
  dotnet-template-repo \
  https://github.com/panixida-templates/dotnet-backend-template.git \
  "$github_secret"

apply_argocd_repository_secret \
  tactical-heroes-api-repo \
  https://github.com/tactical-heroes/api.git \
  "$github_secret"

apply_argocd_repository_secret \
  tactical-heroes-admin-repo \
  https://github.com/tactical-heroes/admin.git \
  "$github_secret"

server_gh_pat="$(jq -r '.SERVER_GH_PAT // empty' <<<"$github_secret")"
registry_user="$(jq -r '.REGISTRY_USER // empty' <<<"$dotnet_template_registry_secret")"
registry_token="$(jq -r '.REGISTRY_TOKEN // empty' <<<"$dotnet_template_registry_secret")"
tactical_heroes_registry_user="$(jq -r '.REGISTRY_USER // empty' <<<"$tactical_heroes_registry_secret")"
tactical_heroes_registry_token="$(jq -r '.REGISTRY_TOKEN // empty' <<<"$tactical_heroes_registry_secret")"

if [ -z "$server_gh_pat" ]; then
  echo "::error::missing required secret key SERVER_GH_PAT"
  exit 1
fi

if [ -z "$registry_user" ] || [ -z "$registry_token" ]; then
  echo "::error::missing required dotnet-template registry credentials"
  exit 1
fi

if [ -z "$tactical_heroes_registry_user" ] || [ -z "$tactical_heroes_registry_token" ]; then
  echo "::error::missing required tactical-heroes registry credentials"
  exit 1
fi

apply_kargo_repository_secret \
  dotnet-template \
  dotnet-template-git \
  git \
  https://github.com/panixida-templates/dotnet-backend-template.git \
  x-access-token \
  "$server_gh_pat"

apply_kargo_repository_secret \
  dotnet-template \
  dotnet-template-api-image \
  image \
  ghcr.io/panixida-templates/dotnet-backend-template/api \
  "$registry_user" \
  "$registry_token"

apply_kargo_repository_secret \
  dotnet-template \
  dotnet-template-migrator-image \
  image \
  ghcr.io/panixida-templates/dotnet-backend-template/ef-migrator \
  "$registry_user" \
  "$registry_token"

apply_kargo_repository_secret \
  tactical-heroes \
  tactical-heroes-git \
  git \
  https://github.com/tactical-heroes/api.git \
  x-access-token \
  "$server_gh_pat"

apply_kargo_repository_secret \
  tactical-heroes \
  tactical-heroes-api-image \
  image \
  ghcr.io/panixida/tactical-heroes/api \
  "$tactical_heroes_registry_user" \
  "$tactical_heroes_registry_token"

apply_kargo_repository_secret \
  tactical-heroes \
  tactical-heroes-migrator-image \
  image \
  ghcr.io/panixida/tactical-heroes/ef-migrator \
  "$tactical_heroes_registry_user" \
  "$tactical_heroes_registry_token"

apply_kargo_repository_secret \
  tactical-heroes-admin \
  tactical-heroes-admin-git \
  git \
  https://github.com/tactical-heroes/admin.git \
  x-access-token \
  "$server_gh_pat"

apply_kargo_repository_secret \
  tactical-heroes-admin \
  tactical-heroes-admin-image \
  image \
  ghcr.io/panixida/tactical-heroes/admin \
  "$tactical_heroes_registry_user" \
  "$tactical_heroes_registry_token"

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
