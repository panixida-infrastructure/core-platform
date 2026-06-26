#!/usr/bin/env bash
set -euo pipefail

core_platform_project_id="${CORE_PLATFORM_PROJECT_ID:-1152653}"

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
  local import_id="$2"
  local expected_id="${3:-$2}"
  local current_id

  if tofu state show "$address" >/dev/null 2>&1; then
    current_id="$(state_id "$address")"
    if [ "$current_id" = "$expected_id" ]; then
      echo "Already imported: ${address}"
      return
    fi

    echo "Replacing import: ${address} ${current_id} -> ${expected_id}"
    tofu state rm "$address"
  fi

  echo "Importing: ${address}"
  tofu import -input=false "$address" "$import_id"
}

remove_if_present twc_project.common
remove_if_present twc_k8s_node_group.core_platform_infrastructure_v2
remove_if_present twc_router.core_platform_msk
remove_if_present twc_floating_ip.core_platform_router_ipv4_msk
import_or_replace twc_project.infrastructure "$core_platform_project_id"
bash "$(dirname "$0")/ensure-k8s-worker-group-state.sh"
