#!/usr/bin/env bash
set -euo pipefail

: "${AWS_ACCESS_KEY_ID:?S3 access key is required}"
: "${AWS_SECRET_ACCESS_KEY:?S3 secret key is required}"

bucket=panixida-storage
rule_file="$(dirname "${BASH_SOURCE[0]}")/storage-noncurrent-versions.json"

s3() {
  aws s3api "$@" --bucket "$bucket" --endpoint-url https://s3.twcstorage.ru \
    --region ru-1 --no-cli-pager
}

read_lifecycle() {
  local response
  if response="$(s3 get-bucket-lifecycle-configuration --output json 2>&1)"; then
    printf '%s\n' "$response"
  elif [[ "$response" == *'(NoSuchLifecycleConfiguration)'* ]]; then
    printf '%s\n' '{"Rules":[]}'
  else
    printf '%s\n' "$response" >&2
    return 1
  fi
}

canonical_rules() {
  # S3-compatible servers may return an empty prefix instead of an empty Filter.
  jq -cS '.Rules | map(
    if .Prefix == "" then del(.Prefix) else . end
    | if .Filter == {} or .Filter == {Prefix:""} then del(.Filter) else . end
  ) | sort_by(.ID)'
}

current_lifecycle="$(read_lifecycle)"
# The S3 PUT replaces the entire lifecycle configuration. Keep every unowned rule.
desired_lifecycle="$(jq -c --slurpfile managed "$rule_file" \
  '.Rules |= (map(select(.ID != $managed[0].ID)) + $managed)' <<<"$current_lifecycle")"
desired_rules="$(canonical_rules <<<"$desired_lifecycle")"
current_rules="$(canonical_rules <<<"$current_lifecycle")"

versioning="$(s3 get-bucket-versioning --query Status --output text)"
if [[ "$versioning" != Enabled ]]; then
  s3 put-bucket-versioning --versioning-configuration Status=Enabled
  versioning="$(s3 get-bucket-versioning --query Status --output text)"
fi
[[ "$versioning" == Enabled ]] || {
  echo 'Bucket versioning readback did not return Enabled' >&2
  exit 1
}

if [[ "$current_rules" != "$desired_rules" ]]; then
  lifecycle_request="$(jq -c '{LifecycleConfiguration:{Rules}} + del(.Rules)' <<<"$desired_lifecycle")"
  s3 put-bucket-lifecycle-configuration --cli-input-json "$lifecycle_request"
fi

applied_lifecycle="$(read_lifecycle)"
[[ "$(canonical_rules <<<"$applied_lifecycle")" == "$desired_rules" ]] || {
  echo 'Bucket lifecycle readback differs from the desired rules' >&2
  exit 1
}

printf 'Bucket %s versioning is Enabled. Lifecycle configuration:\n' "$bucket"
jq . <<<"$applied_lifecycle"
