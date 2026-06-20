#!/usr/bin/env bash
set -euo pipefail

address="${K8S_WORKER_GROUP_ADDRESS:-twc_k8s_node_group.core_platform_default}"
resource_name="${K8S_WORKER_GROUP_RESOURCE_NAME:-core_platform_default}"
cluster_id="${CORE_PLATFORM_CLUSTER_ID:-1091532}"
worker_group_id="${CORE_PLATFORM_WORKER_GROUP_ID:-113109}"
worker_group_name="${CORE_PLATFORM_WORKER_GROUP_NAME:-core-platform-infrastructure}"
worker_group_node_count="${CORE_PLATFORM_WORKER_GROUP_NODE_COUNT:-3}"
worker_group_preset_id="${CORE_PLATFORM_WORKER_GROUP_PRESET_ID:-2951}"
router_id="${CORE_PLATFORM_ROUTER_ID:-1c056bf0-6095-433d-ad06-f76c70eafc92}"

state_id() {
  local state_address="$1"

  tofu state show -no-color "$state_address" \
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

if tofu state show "$address" >/dev/null 2>&1; then
  current_id="$(state_id "$address")"
  if [ "$current_id" = "$worker_group_id" ]; then
    echo "Already present in state: ${address}"
    exit 0
  fi

  echo "Removing stale worker group state: ${address} ${current_id} -> ${worker_group_id}"
  tofu state rm "$address"
fi

state_file="$(mktemp)"
patched_state_file="$(mktemp)"
trap 'rm -f "$state_file" "$patched_state_file"' EXIT

tofu state pull >"$state_file"

jq \
  --arg resource_name "$resource_name" \
  --arg cluster_id "$cluster_id" \
  --arg worker_group_id "$worker_group_id" \
  --arg worker_group_name "$worker_group_name" \
  --arg worker_group_node_count "$worker_group_node_count" \
  --arg worker_group_preset_id "$worker_group_preset_id" \
  --arg router_id "$router_id" \
  '
  .serial = ((.serial // 0) + 1)
  | .resources = (
      (.resources // [])
      | map(select(.type != "twc_k8s_node_group" or .name != $resource_name))
    )
  | .resources += [
      {
        "mode": "managed",
        "type": "twc_k8s_node_group",
        "name": $resource_name,
        "provider": "provider[\"tf.timeweb.cloud/timeweb-cloud/timeweb-cloud\"]",
        "instances": [
          {
            "schema_version": 0,
            "attributes": {
              "cluster_id": ($cluster_id | tonumber),
              "configuration": [],
              "id": $worker_group_id,
              "is_autohealing": true,
              "is_autoscaling": false,
              "labels": [
                {
                  "key": "panixida.ru/node-pool",
                  "value": "core-platform"
                }
              ],
              "max_size": null,
              "min_size": null,
              "name": $worker_group_name,
              "node_count": ($worker_group_node_count | tonumber),
              "preset_id": ($worker_group_preset_id | tonumber),
              "public_ip_enabled": false,
              "taints": [],
              "virtual_router_id": $router_id
            },
            "sensitive_attributes": []
          }
        ]
      }
    ]
  ' "$state_file" >"$patched_state_file"

tofu state push "$patched_state_file"
echo "Added to state: ${address} ${worker_group_id}"
