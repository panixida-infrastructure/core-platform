#!/usr/bin/env bash
set -euo pipefail

core_platform_project_id="${CORE_PLATFORM_PROJECT_ID:-1152653}"
core_platform_cluster_id="${CORE_PLATFORM_CLUSTER_ID:-1091532}"
core_platform_worker_group_id="${CORE_PLATFORM_WORKER_GROUP_ID:-113109}"

state_id() {
  local address="$1"

  tofu state show -no-color "$address" \
    | awk -F' = ' '{
      key = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      if (key == "id") {
        gsub(/"/, "", $2)
        print $2
        exit
      }
    }'
}

remove_if_present() {
  local address="$1"

  if tofu state show "$address" >/dev/null 2>&1; then
    echo "Removing from state: ${address}"
    tofu state rm "$address"
  fi
}

import_or_replace() {
  local address="$1"
  local id="$2"
  local current_id

  if tofu state show "$address" >/dev/null 2>&1; then
    current_id="$(state_id "$address")"
    if [ "$current_id" = "$id" ]; then
      echo "Already imported: ${address}"
      return
    fi

    echo "Replacing import: ${address} ${current_id} -> ${id}"
    tofu state rm "$address"
  fi

  echo "Importing: ${address}"
  tofu import -input=false "$address" "$id"
}

remove_if_present twc_project.common
import_or_replace twc_project.infrastructure "$core_platform_project_id"
import_or_replace twc_k8s_node_group.core_platform_default "${core_platform_cluster_id}/${core_platform_worker_group_id}"
