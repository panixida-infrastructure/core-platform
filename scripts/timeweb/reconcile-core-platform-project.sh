#!/usr/bin/env bash
set -euo pipefail

timeweb_api="${TIMEWEB_API:-https://api.timeweb.cloud}"
source_project_id="${CORE_PLATFORM_SOURCE_PROJECT_ID:-1619863}"
target_project_id="${CORE_PLATFORM_TARGET_PROJECT_ID:-1152653}"
target_project_name="${CORE_PLATFORM_PROJECT_NAME:-core-platform}"
retired_project_name="${CORE_PLATFORM_RETIRED_PROJECT_NAME:-core-platform-retired-1619863}"

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
  local status
  local response

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

  if [ "$status" = "404" ]; then
    rm -f "$response"
    return 44
  fi

  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    cat "$response" >&2
    rm -f "$response"
    return 1
  fi

  cat "$response"
  rm -f "$response"
}

json_escape() {
  jq -Rn --arg value "$1" '$value'
}

get_project() {
  local project_id="$1"
  local project
  local status

  set +e
  project="$(twc GET "/api/v1/projects/${project_id}")"
  status="$?"
  set -e

  if [ "$status" = "44" ]; then
    return 0
  fi

  if [ "$status" -ne 0 ]; then
    return "$status"
  fi

  printf '%s' "$project"
}

rename_project() {
  local project_id="$1"
  local name="$2"
  local project
  local current_name

  project="$(get_project "$project_id")"
  if [ -z "$project" ]; then
    return
  fi

  current_name="$(jq -r '.project.name' <<<"$project")"
  if [ "$current_name" = "$name" ]; then
    return
  fi

  echo "Renaming project ${project_id} to ${name}"
  twc PUT "/api/v1/projects/${project_id}" "{\"name\":$(json_escape "$name")}" >/dev/null
}

transfer_resource() {
  local from_project_id="$1"
  local resource_type="$2"
  local resource_id="$3"

  echo "Moving ${resource_type} ${resource_id} to project ${target_project_id}"
  twc PUT \
    "/api/v1/projects/${from_project_id}/resources/transfer" \
    "{\"to_project\":${target_project_id},\"resource_id\":${resource_id},\"resource_type\":\"${resource_type}\"}" \
    >/dev/null
}

transfer_resources() {
  local resources
  local status

  set +e
  resources="$(twc GET "/api/v1/projects/${source_project_id}/resources")"
  status="$?"
  set -e

  if [ "$status" -ne 0 ]; then
    if [ "$status" = "44" ]; then
      return
    fi

    return 1
  fi

  if [ -z "$resources" ]; then
    return
  fi

  jq -r '.servers[]?.id' <<<"$resources" | while IFS= read -r resource_id; do
    [ -z "$resource_id" ] && continue
    transfer_resource "$source_project_id" server "$resource_id"
  done

  jq -r '.buckets[]?.id' <<<"$resources" | while IFS= read -r resource_id; do
    [ -z "$resource_id" ] && continue
    transfer_resource "$source_project_id" storage "$resource_id"
  done

  jq -r '.databases[]?.id' <<<"$resources" | while IFS= read -r resource_id; do
    [ -z "$resource_id" ] && continue
    transfer_resource "$source_project_id" database "$resource_id"
  done

  jq -r '.balancers[]?.id' <<<"$resources" | while IFS= read -r resource_id; do
    [ -z "$resource_id" ] && continue
    transfer_resource "$source_project_id" balancer "$resource_id"
  done

  jq -r '.clusters[]?.id' <<<"$resources" | while IFS= read -r resource_id; do
    [ -z "$resource_id" ] && continue
    transfer_resource "$source_project_id" kubernetes "$resource_id"
  done

  jq -r '.dedicated_servers[]?.id' <<<"$resources" | while IFS= read -r resource_id; do
    [ -z "$resource_id" ] && continue
    transfer_resource "$source_project_id" dedicated "$resource_id"
  done
}

delete_source_project() {
  local source_project

  source_project="$(get_project "$source_project_id")"
  if [ -z "$source_project" ]; then
    return
  fi

  echo "Deleting retired project ${source_project_id}"
  twc DELETE "/api/v1/projects/${source_project_id}" >/dev/null
}

require_env TIMEWEB_TOKEN

target_project="$(get_project "$target_project_id")"
if [ -z "$target_project" ]; then
  echo "::error::Target project ${target_project_id} does not exist"
  exit 1
fi

source_project="$(get_project "$source_project_id")"
if [ -n "$source_project" ]; then
  rename_project "$source_project_id" "$retired_project_name"
  transfer_resources
fi

rename_project "$target_project_id" "$target_project_name"
delete_source_project

echo "Core platform project reconciliation completed"
