#!/usr/bin/env bash
set -euo pipefail

openbao_addr="${OPENBAO_ADDR:-https://secrets.panixida.ru}"
openbao_role="${OPENBAO_ROLE:-core-platform-github-actions}"
openbao_audience="${OPENBAO_AUDIENCE:-https://github.com/panixida-infrastructure/core-platform}"
sonar_url="${SONAR_URL:-https://sonar.panixida.ru}"
sonar_github_integration_key="${SONAR_GITHUB_INTEGRATION_KEY:-GitHub}"
sonar_quality_gate="${SONAR_QUALITY_GATE:-Sonar way}"
repository_filter="${REPOSITORY_FILTER:-}"
rotate_tokens="${ROTATE_SONAR_TOKENS:-false}"
inventory_file="${1:-inventory/sonarqube/repositories.json}"

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "::error::${command_name} is required"
    exit 1
  fi
}

require_secret_key() {
  local json="$1"
  local key="$2"
  if [[ "$(jq -r --arg key "$key" '.[$key] // empty' <<<"$json")" == "" ]]; then
    echo "::error::OpenBao secret is missing ${key}"
    exit 1
  fi
}

openbao_login() {
  if [[ -z "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" || -z "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]]; then
    echo "::error::GitHub Actions OIDC environment is required"
    exit 1
  fi

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

openbao_read() {
  local token="$1"
  local path="$2"

  curl -fsS \
    -H "X-Vault-Token: ${token}" \
    "${openbao_addr}/v1/secret/data/${path}" | jq '.data.data // {}'
}

base64_urlencode() {
  openssl base64 -A | tr '+/' '-_' | tr -d '='
}

create_github_app_jwt() {
  local app_id="$1"
  local private_key_file="$2"
  local issued_at
  local expires_at
  local header
  local payload
  local unsigned_token
  local signature

  issued_at="$(( $(date +%s) - 60 ))"
  expires_at="$(( issued_at + 540 ))"
  header="$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | base64_urlencode)"
  payload="$(jq -nc \
    --argjson iat "$issued_at" \
    --argjson exp "$expires_at" \
    --arg iss "$app_id" \
    '{iat: $iat, exp: $exp, iss: $iss}' | base64_urlencode)"
  unsigned_token="${header}.${payload}"
  signature="$(printf '%s' "$unsigned_token" \
    | openssl dgst -sha256 -sign "$private_key_file" \
    | base64_urlencode)"

  printf '%s.%s' "$unsigned_token" "$signature"
}

create_installation_token() {
  local app_jwt="$1"
  local installation_id="$2"
  local repository_name="$3"
  local payload

  payload="$(jq -nc --arg repository "$repository_name" '{repositories: [$repository]}')"
  curl -fsS \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${app_jwt}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "https://api.github.com/app/installations/${installation_id}/access_tokens" \
    | jq -r '.token'
}

for command_name in curl gh jq openssl sort uniq; do
  require_command "$command_name"
done

bash scripts/sonarqube/validate-repositories.sh "$inventory_file"

if [[ "$rotate_tokens" != "true" && "$rotate_tokens" != "false" ]]; then
  echo "::error::ROTATE_SONAR_TOKENS must be true or false"
  exit 1
fi

if [[ -n "$repository_filter" ]]; then
  matching_repositories="$(jq -r --arg repository "$repository_filter" \
    '[.repositories[] | select((.repository | ascii_downcase) == ($repository | ascii_downcase))] | length' \
    "$inventory_file")"
  if [[ "$matching_repositories" != "1" ]]; then
    echo "::error::Repository is not present in the SonarQube inventory: ${repository_filter}"
    exit 1
  fi
fi

openbao_token="$(openbao_login)"
sonar_secret="$(openbao_read "$openbao_token" core-platform/sonarqube)"
github_provisioner_secret="$(openbao_read "$openbao_token" core-platform/github-provisioner)"
unset openbao_token

require_secret_key "$sonar_secret" SONAR_ADMIN_PASSWORD
require_secret_key "$github_provisioner_secret" GITHUB_APP_ID
require_secret_key "$github_provisioner_secret" GITHUB_APP_PRIVATE_KEY

sonar_admin_password="$(jq -r '.SONAR_ADMIN_PASSWORD' <<<"$sonar_secret")"
github_app_id="$(jq -r '.GITHUB_APP_ID' <<<"$github_provisioner_secret")"

temporary_directory="$(mktemp -d)"
github_private_key_file="${temporary_directory}/github-app.pem"
trap 'rm -rf "$temporary_directory"' EXIT
jq -r '.GITHUB_APP_PRIVATE_KEY' <<<"$github_provisioner_secret" >"$github_private_key_file"
chmod 600 "$github_private_key_file"
unset sonar_secret github_provisioner_secret

github_app_jwt="$(create_github_app_jwt "$github_app_id" "$github_private_key_file")"
installations="$(curl -fsS \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${github_app_jwt}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/app/installations?per_page=100")"

if ! curl -fsS -u "admin:${sonar_admin_password}" \
  "${sonar_url}/api/authentication/validate" | jq -e '.valid == true' >/dev/null; then
  echo "::error::SonarQube administrator credentials are not valid"
  exit 1
fi

dop_settings="$(curl -fsS -u "admin:${sonar_admin_password}" \
  "${sonar_url}/api/v2/dop-translation/dop-settings")"
github_dop_setting_count="$(jq -r \
  --arg key "$sonar_github_integration_key" \
  '[.dopSettings[] | select(.type == "github" and .key == $key)] | length' \
  <<<"$dop_settings")"
if [[ "$github_dop_setting_count" != "1" ]]; then
  echo "::error::Expected exactly one GitHub DevOps Platform setting named ${sonar_github_integration_key}"
  exit 1
fi
github_dop_setting_id="$(jq -r \
  --arg key "$sonar_github_integration_key" \
  '.dopSettings[] | select(.type == "github" and .key == $key) | .id' \
  <<<"$dop_settings")"
unset dop_settings github_dop_setting_count

mapfile -t repository_entries < <(jq -c \
  --arg repository "$repository_filter" \
  '.repositories[] | select($repository == "" or ((.repository | ascii_downcase) == ($repository | ascii_downcase)))' \
  "$inventory_file")
declare -A configured_organizations=()

for entry in "${repository_entries[@]}"; do
  repository="$(jq -r '.repository' <<<"$entry")"
  project_key="$(jq -r '.projectKey' <<<"$entry")"
  project_name="$(jq -r '.projectName' <<<"$entry")"
  repository_owner="${repository%%/*}"
  repository_name="${repository#*/}"
  token_name="github-actions-${project_key}"

  echo "Reconciling ${repository} as SonarQube project ${project_key}"

  installation_id="$(jq -r \
    --arg owner "$repository_owner" \
    '.[] | select((.account.login | ascii_downcase) == ($owner | ascii_downcase)) | .id' \
    <<<"$installations" | head -n1)"
  if [[ -z "$installation_id" || "$installation_id" == "null" ]]; then
    echo "::error::GitHub App is not installed for ${repository_owner}"
    exit 1
  fi

  installation_token="$(create_installation_token "$github_app_jwt" "$installation_id" "$repository_name")"
  if [[ -z "$installation_token" || "$installation_token" == "null" ]]; then
    echo "::error::Failed to create a GitHub installation token for ${repository}"
    exit 1
  fi

  actual_repository="$(GH_TOKEN="$installation_token" gh api "repos/${repository}" --jq '.full_name')"
  if [[ "${actual_repository,,}" != "${repository,,}" ]]; then
    echo "::error::GitHub repository identity mismatch for ${repository}"
    exit 1
  fi

  if [[ -z "${configured_organizations[$repository_owner]+x}" ]]; then
    GH_TOKEN="$installation_token" gh variable set SONAR_HOST_URL \
      --body "$sonar_url" \
      --org "$repository_owner" \
      --visibility all
    organization_sonar_url="$(GH_TOKEN="$installation_token" gh api \
      "orgs/${repository_owner}/actions/variables/SONAR_HOST_URL" \
      --jq '.value')"
    if [[ "$organization_sonar_url" != "$sonar_url" ]]; then
      echo "::error::Organization SONAR_HOST_URL mismatch for ${repository_owner}"
      exit 1
    fi
    configured_organizations[$repository_owner]=true
    echo "Reconciled organization SONAR_HOST_URL for ${repository_owner}"
  fi

  curl -fsS -u "admin:${sonar_admin_password}" -X POST \
    "${sonar_url}/api/alm_integrations/set_pat" \
    --data-urlencode "almSetting=${sonar_github_integration_key}" \
    --data-urlencode "pat=${installation_token}" >/dev/null

  bound_project_payload="$(jq -nc \
    --arg project_key "$project_key" \
    --arg project_name "$project_name" \
    --arg dop_setting_id "$github_dop_setting_id" \
    --arg repository "$repository" \
    '{
      projectKey: $project_key,
      projectName: $project_name,
      devOpsPlatformSettingId: $dop_setting_id,
      repositoryIdentifier: $repository,
      projectIdentifier: null,
      newCodeDefinitionType: "PREVIOUS_VERSION",
      newCodeDefinitionValue: null,
      monorepo: false
    }')"
  bound_project_response="$(curl -fsS -u "admin:${sonar_admin_password}" -X PUT \
    -H "Content-Type: application/json" \
    -d "$bound_project_payload" \
    "${sonar_url}/api/v2/dop-translation/bound-projects")"
  if [[ "$(jq -r '.newProjectCreated' <<<"$bound_project_response")" == "true" ]]; then
    echo "Created and bound SonarQube project ${project_key}"
  else
    echo "Updated GitHub binding for SonarQube project ${project_key}"
  fi

  curl -fsS -u "admin:${sonar_admin_password}" -X POST \
    "${sonar_url}/api/projects/update_visibility" \
    --data-urlencode "project=${project_key}" \
    --data-urlencode "visibility=private" >/dev/null

  curl -fsS -u "admin:${sonar_admin_password}" -X POST \
    "${sonar_url}/api/qualitygates/select" \
    --data-urlencode "gateName=${sonar_quality_gate}" \
    --data-urlencode "projectKey=${project_key}" >/dev/null

  binding_repository="$(curl -fsS -u "admin:${sonar_admin_password}" --get \
    "${sonar_url}/api/alm_settings/get_binding" \
    --data-urlencode "project=${project_key}" | jq -r '.repository // empty')"
  if [[ "${binding_repository,,}" != "${repository,,}" ]]; then
    echo "::error::SonarQube binding mismatch for ${project_key}"
    exit 1
  fi

  token_search="$(curl -fsS -u "admin:${sonar_admin_password}" \
    "${sonar_url}/api/user_tokens/search")"
  named_token_exists="$(jq -r \
    --arg name "$token_name" \
    'any(.userTokens[]?; .name == $name)' <<<"$token_search")"
  matching_token_exists="$(jq -r \
    --arg name "$token_name" \
    --arg project "$project_key" \
    'any(.userTokens[]?; .name == $name and .type == "PROJECT_ANALYSIS_TOKEN" and .project.key == $project)' \
    <<<"$token_search")"

  if GH_TOKEN="$installation_token" gh api \
    "repos/${repository}/actions/secrets/SONAR_TOKEN" >/dev/null 2>&1; then
    github_secret_exists=true
  else
    github_secret_exists=false
  fi

  if [[ "$rotate_tokens" == "true" || "$matching_token_exists" != "true" || "$github_secret_exists" != "true" ]]; then
    if [[ "$named_token_exists" == "true" ]]; then
      curl -fsS -u "admin:${sonar_admin_password}" -X POST \
        "${sonar_url}/api/user_tokens/revoke" \
        --data-urlencode "name=${token_name}" >/dev/null
    fi

    generated_token="$(curl -fsS -u "admin:${sonar_admin_password}" -X POST \
      "${sonar_url}/api/user_tokens/generate" \
      --data-urlencode "name=${token_name}" \
      --data-urlencode "type=PROJECT_ANALYSIS_TOKEN" \
      --data-urlencode "projectKey=${project_key}" | jq -r '.token')"
    if [[ -z "$generated_token" || "$generated_token" == "null" ]]; then
      echo "::error::SonarQube did not return an analysis token for ${project_key}"
      exit 1
    fi

    printf '%s' "$generated_token" \
      | GH_TOKEN="$installation_token" gh secret set SONAR_TOKEN --repo "$repository"
    unset generated_token
    echo "Created or rotated SONAR_TOKEN for ${repository}"
  else
    echo "Kept existing SONAR_TOKEN for ${repository}"
  fi

  GH_TOKEN="$installation_token" gh variable set SONAR_PROJECT_KEY \
    --body "$project_key" --repo "$repository"

  if GH_TOKEN="$installation_token" gh api \
    "repos/${repository}/actions/variables/SONAR_HOST_URL" >/dev/null 2>&1; then
    GH_TOKEN="$installation_token" gh variable delete SONAR_HOST_URL --repo "$repository"
    echo "Removed repository-level SONAR_HOST_URL from ${repository}"
  fi

  unset installation_token token_search bound_project_payload bound_project_response binding_repository organization_sonar_url
  echo "Reconciled ${repository}"
done

echo "SonarQube repository reconciliation completed"
