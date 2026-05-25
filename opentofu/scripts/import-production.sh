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

import_if_missing twc_project.common 1152653
import_if_missing twc_project.infrastructure 1619863

import_if_missing twc_server.infrastructure 8034806

import_if_missing twc_database_cluster.postgres_database_legacy 4104619

import_if_missing twc_ssh_key.infrastructure_605568 605568

import_if_missing twc_floating_ip.infrastructure_ipv4 4d2c3cc1-3172-4fdd-a78b-7bada0d65a41
import_if_missing twc_floating_ip.postgres_database_ipv4_legacy b74e37e1-de83-4fac-9251-3061433b24bc
import_if_missing twc_floating_ip.postgres_database_ipv4_msk 8f61d71c-21f3-40e7-af2a-1e762ecb9448
