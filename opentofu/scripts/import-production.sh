#!/usr/bin/env bash
set -euo pipefail

import_if_missing() {
  local address="$1"
  local id="$2"

  if tofu state show "$address" >/dev/null 2>&1; then
    echo "Already imported: ${address}"
    return
  fi

  echo "Importing: ${address}"
  tofu import -input=false "$address" "$id"
}

import_or_replace() {
  local address="$1"
  local import_id="$2"
  local expected_id="${3:-$2}"
  local current_id

  if tofu state show "$address" >/dev/null 2>&1; then
    current_id="$(
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
    )"
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

import_or_replace twc_project.infrastructure 1152653
import_or_replace twc_k8s_cluster.core_platform 1091532
import_or_replace twc_k8s_node_group.core_platform_default "113109?cluster_id=1091532" 113109

import_or_replace twc_floating_ip.postgres_database_ipv4_msk fc66efd9-a4a1-4983-bbd4-40fdaa70c46f
