#!/usr/bin/env bash
set -euo pipefail

api_server="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
api_endpoint="${api_server#*://}"
api_host="${api_endpoint%%:*}"
api_port="${api_endpoint##*:}"

if [ -z "$api_host" ]; then
  echo "::error::Kubernetes API host is empty"
  exit 1
fi

if [ "$api_port" = "$api_endpoint" ]; then
  api_port=443
fi

if ! [[ "$api_port" =~ ^[0-9]+$ ]]; then
  echo "::error::Kubernetes API port is invalid"
  exit 1
fi

cilium_patch="$(jq -nc \
  --arg host "$api_host" \
  --arg port "$api_port" '
    {
      spec: {
        template: {
          spec: {
            initContainers: [{
              name: "config",
              env: [
                {name: "KUBERNETES_SERVICE_HOST", value: $host},
                {name: "KUBERNETES_SERVICE_PORT", value: $port}
              ]
            }],
            containers: [{
              name: "cilium-agent",
              env: [
                {name: "KUBERNETES_SERVICE_HOST", value: $host},
                {name: "KUBERNETES_SERVICE_PORT", value: $port}
              ]
            }]
          }
        }
      }
    }')"

kubectl -n kube-system patch daemonset cilium \
  --type=strategic \
  --patch "$cilium_patch"
kubectl -n kube-system set env deployment/cilium-operator \
  KUBERNETES_SERVICE_HOST- \
  KUBERNETES_SERVICE_PORT-

kubectl -n kube-system rollout status daemonset/cilium --timeout=5m
kubectl -n kube-system rollout status deployment/cilium-operator --timeout=5m
