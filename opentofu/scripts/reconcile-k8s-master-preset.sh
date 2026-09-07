#!/usr/bin/env bash
set -euo pipefail

# twc_k8s_cluster currently acknowledges preset updates without resizing the
# master. Reconcile through the dedicated API using the applied plan's preset.
cluster_id="${1:?Cluster ID is required}"
preset_id="${2:?Master preset ID is required}"
api="${TIMEWEB_API:-https://api.timeweb.cloud}"
timeout="${MASTER_PRESET_TIMEOUT_SECONDS:-1200}"
interval="${MASTER_PRESET_POLL_SECONDS:-15}"
: "${TIMEWEB_TOKEN:?TIMEWEB_TOKEN is required}"

[[ "$cluster_id" =~ ^[0-9]+$ && "$preset_id" =~ ^[0-9]+$ ]] || {
  echo "Cluster and preset IDs must be integers" >&2
  exit 1
}

request() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-fsS --connect-timeout 10 --max-time 30
    -H "Authorization: Bearer ${TIMEWEB_TOKEN}" -H 'Content-Type: application/json')
  if [ -n "$body" ]; then args+=(-d "$body"); fi
  curl "${args[@]}" -X "$method" "${api}${path}"
}

cluster="$(request GET "/api/v1/k8s/clusters/${cluster_id}" | jq -e '.cluster')"
current_id="$(jq -er '.preset_id' <<<"$cluster")"
presets="$(request GET /api/v1/presets/k8s | jq -e '.k8s_presets')"
target="$(jq -ce --argjson id "$preset_id" '.[] | select(.id == $id and .type == "master")' <<<"$presets")"
zone="$(jq -er '.availability_zone' <<<"$cluster")"
if ! jq -e --arg zone "$zone" '.availability_zone == $zone' <<<"$target" >/dev/null; then
  echo "Master preset belongs to a different availability zone" >&2
  exit 1
fi

if [ "$current_id" != "$preset_id" ]; then
  current="$(jq -ce --argjson id "$current_id" '.[] | select(.id == $id and .type == "master")' <<<"$presets")"
  if ! jq -e --argjson current "$current" '
    .cpu >= $current.cpu and .ram >= $current.ram and .disk >= $current.disk
    and .master_nodes_count == $current.master_nodes_count
  ' <<<"$target" >/dev/null; then
    echo "Refusing to reduce master resources or change the number of masters" >&2
    exit 1
  fi
  if [ "$(jq -r '.status' <<<"$cluster")" != started ]; then
    echo "Cluster is busy; refusing to submit another master resize" >&2
    exit 1
  fi
  echo "Resizing cluster ${cluster_id} master preset ${current_id} -> ${preset_id}"
  request PATCH "/api/v1/k8s/clusters/${cluster_id}/master-nodes" \
    "$(jq -cn --argjson id "$preset_id" '{preset_id: $id}')" >/dev/null
fi

deadline=$((SECONDS + timeout))
while :; do
  cluster="$(request GET "/api/v1/k8s/clusters/${cluster_id}" | jq -e '.cluster')"
  nodes="$(request GET "/api/v1/k8s/clusters/${cluster_id}/master-nodes" | jq -e '.nodes')"
  if jq -e --argjson id "$preset_id" '.preset_id == $id and .status == "started"' <<<"$cluster" >/dev/null \
    && jq -e --argjson target "$target" '
      length == $target.master_nodes_count and all(.[];
        .preset_id == $target.id and .status == "active"
        and .cpu == $target.cpu and .ram == $target.ram and .disk == $target.disk)
    ' <<<"$nodes" >/dev/null; then
    echo "Verified cluster ${cluster_id}: master preset ${preset_id}, $(jq -r '"\(.cpu) CPU / \(.ram) MiB RAM / \(.disk) GiB disk"' <<<"$target")"
    exit 0
  fi
  if (( SECONDS >= deadline )); then
    echo "Timed out waiting for the requested master resources; verify Timeweb before retrying" >&2
    exit 1
  fi
  echo "Waiting for master preset ${preset_id} to become active..."
  sleep "$interval"
done
