#!/usr/bin/env bash
set -euo pipefail

timeweb_api="${TIMEWEB_API:-https://api.timeweb.cloud}"
retired_floating_ip_ids="${RETIRED_FLOATING_IP_IDS:-0be69046-72d8-4351-b812-99cdada61745 0c8f6e70-f35e-4739-94b2-801dcf2c646a 496a8dcc-e706-4796-b9a2-578d4063a459 8f61d71c-21f3-40e7-af2a-1e762ecb9448 926efd13-f45e-4b2f-89d3-66aee502a685 a69fc138-6002-4ade-aab2-21603ace6d50 dae55c1e-300b-4883-ac44-177b4d5e198b 19520275-e6da-4cd4-91e8-ba571edca73f 43a7152a-74c7-4e48-8805-91c1a529d562 454bcb89-e302-45b0-99c8-22585c10b78f}"
retired_storage_bucket_ids="${RETIRED_STORAGE_BUCKET_IDS:-344103}"
current_storage_bucket_name="${CURRENT_STORAGE_BUCKET_NAME:-panixida-storage}"
retired_panixida_subdomains="${RETIRED_PANIXIDA_SUBDOMAINS:-alerts auth komodo logs metrics traces traefik portainer}"

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

  if [ "$status" = "403" ] && jq -e '.error_code == "storage_action_are_prohibited"' "$response" >/dev/null 2>&1; then
    cat "$response" >&2
    rm -f "$response"
    return 43
  fi

  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    cat "$response" >&2
    rm -f "$response"
    return 1
  fi

  cat "$response"
  rm -f "$response"
}

delete_dns_records_for_subdomain() {
  local fqdn="$1"
  local subdomain="$2"
  local records

  records="$(twc GET "/api/v1/domains/${fqdn}/dns-records?limit=200" || true)"
  if [ -z "$records" ]; then
    return
  fi

  jq -r --arg subdomain "$subdomain" \
    '.dns_records[]? | select((.subdomain // "") == $subdomain) | .id' \
    <<<"$records" \
    | while IFS= read -r record_id; do
      [ -z "$record_id" ] && continue
      echo "Deleting DNS record ${record_id} for ${subdomain}.${fqdn}"
      twc DELETE "/api/v1/domains/${fqdn}/dns-records/${record_id}" >/dev/null || true
    done
}

delete_domain_subdomain() {
  local fqdn="$1"
  local subdomain="$2"
  local domain

  delete_dns_records_for_subdomain "$fqdn" "$subdomain"

  domain="$(twc GET "/api/v1/domains/${fqdn}" || true)"
  if [ -n "$domain" ]; then
    jq -r --arg full "${subdomain}.${fqdn}" \
      '.domain.subdomains[]? | select(.fqdn == $full) | .id' \
      <<<"$domain" \
      | while IFS= read -r subdomain_id; do
        [ -z "$subdomain_id" ] && continue
        echo "Deleting Timeweb subdomain ${subdomain}.${fqdn} (${subdomain_id})"
        twc DELETE "/api/v1/domains/${fqdn}/subdomains/${subdomain_id}" >/dev/null || true
      done
  fi

  twc DELETE "/api/v1/domains/${fqdn}/subdomains/${subdomain}" >/dev/null || true
  twc DELETE "/api/v1/domains/${fqdn}/subdomains/${subdomain}.${fqdn}" >/dev/null || true
}

active_k8s_node_ids() {
  twc GET "/api/v1/k8s/clusters?limit=200" \
    | jq -r '.clusters[]?.worker_nodes_ids[]?'
}

delete_retired_floating_ip() {
  local floating_ip_id="$1"
  local floating_ip
  local bound_resource_type
  local bound_resource

  floating_ip="$(twc GET "/api/v1/floating-ips/${floating_ip_id}" || true)"
  if [ -z "$floating_ip" ]; then
    return
  fi

  bound_resource_type="$(jq -r '.ip.resource_type // empty' <<<"$floating_ip")"
  bound_resource="$(jq -r '.ip.resource_id // empty' <<<"$floating_ip")"

  if [ -z "$bound_resource" ]; then
    echo "Deleting retired unbound floating IP ${floating_ip_id}"
    twc DELETE "/api/v1/floating-ips/${floating_ip_id}" >/dev/null || true
    return
  fi

  if [ "$bound_resource_type" = "k8s_node" ] \
    && ! grep -Fx "$bound_resource" <<<"$current_k8s_node_ids" >/dev/null; then
    echo "Deleting retired floating IP ${floating_ip_id} bound to missing Kubernetes node ${bound_resource}"
    twc DELETE "/api/v1/floating-ips/${floating_ip_id}" >/dev/null || true
    return
  fi

  if [ -n "$bound_resource" ]; then
    echo "Skipping bound floating IP ${floating_ip_id}"
    return
  fi
}

delete_storage_bucket() {
  local bucket_id="$1"
  local bucket
  local status

  bucket="$(twc GET "/api/v1/storages/buckets/${bucket_id}" || true)"
  if [ -z "$bucket" ]; then
    return
  fi

  echo "Deleting retired storage bucket ${bucket_id}"
  set +e
  twc DELETE "/api/v1/storages/buckets/${bucket_id}" >/dev/null
  status="$?"
  set -e

  if [ "$status" = "43" ]; then
    echo "Skipping retired storage bucket ${bucket_id}: storage is in quarantine"
    return
  fi

  return "$status"
}

clear_storage_bucket_description() {
  local bucket_name="$1"
  local bucket_id

  bucket_id="$(twc GET "/api/v1/storages/buckets?limit=200" \
    | jq -r --arg name "$bucket_name" '.buckets[] | select(.name == $name) | .id' \
    | head -n1)"

  if [ -z "$bucket_id" ]; then
    echo "::error::Storage bucket ${bucket_name} was not found"
    exit 1
  fi

  echo "Clearing storage bucket description for ${bucket_name}"
  twc PATCH "/api/v1/storages/buckets/${bucket_id}" '{"description":""}' >/dev/null
}

require_env TIMEWEB_TOKEN

echo "Enabling panixida.ru autoprolong"
twc PATCH "/api/v1/domains/panixida.ru" '{"is_autoprolong_enabled":true}' >/dev/null

clear_storage_bucket_description "$current_storage_bucket_name"
current_k8s_node_ids="$(active_k8s_node_ids)"

for subdomain in $retired_panixida_subdomains; do
  echo "Deleting ${subdomain}.panixida.ru DNS records and Timeweb subdomain"
  delete_domain_subdomain panixida.ru "$subdomain"
done

echo "Deleting tacticalheroesdev.ru domain"
twc DELETE "/api/v1/domains/tacticalheroesdev.ru" >/dev/null || true

for floating_ip_id in $retired_floating_ip_ids; do
  delete_retired_floating_ip "$floating_ip_id"
done

for bucket_id in $retired_storage_bucket_ids; do
  delete_storage_bucket "$bucket_id"
done

if [ "${DELETE_LEGACY_SPB_NETWORK:-false}" = "true" ]; then
  legacy_network_id="${LEGACY_SPB_NETWORK_ID:-network-f6c0d7e22f5f4d2d8e8df421aa68935d}"
  echo "Deleting legacy SPB network ${legacy_network_id}"
  twc DELETE "/api/v1/vpcs/${legacy_network_id}" >/dev/null || true
fi

echo "Retired Timeweb resources cleanup completed"
