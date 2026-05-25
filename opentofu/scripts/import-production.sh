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
  local id="$2"
  local current_id

  if tofu state show "$address" >/dev/null 2>&1; then
    current_id="$(tofu state show -no-color "$address" | awk -F' = ' '$1 ~ /^id$/ { gsub(/"/, "", $2); print $2; exit }')"
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

import_if_missing twc_project.common 1152653
import_if_missing twc_project.infrastructure 1619863

import_if_missing twc_server.infrastructure 8034806

import_if_missing twc_database_cluster.postgres_database_legacy 4104619

import_if_missing twc_ssh_key.infrastructure_605568 605568

import_if_missing twc_floating_ip.infrastructure_ipv4 4d2c3cc1-3172-4fdd-a78b-7bada0d65a41
import_if_missing twc_floating_ip.postgres_database_ipv4_legacy b74e37e1-de83-4fac-9251-3061433b24bc
import_or_replace twc_floating_ip.postgres_database_ipv4_msk fc66efd9-a4a1-4983-bbd4-40fdaa70c46f
