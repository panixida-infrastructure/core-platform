#!/usr/bin/env bash
set -euo pipefail

inventory_file="${1:-inventory/sonarqube/repositories.json}"

if [[ ! -f "$inventory_file" ]]; then
  echo "::error::SonarQube repository inventory not found: ${inventory_file}"
  exit 1
fi

if ! jq -e '
  type == "object" and
  (.repositories | type == "array") and
  all(.repositories[];
    (type == "object") and
    (.repository | type == "string" and test("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")) and
    (.projectKey | type == "string" and test("^[A-Za-z0-9._:-]+$") and test("[^0-9]")) and
    (.projectName | type == "string" and length > 0)
  )
' "$inventory_file" >/dev/null; then
  echo "::error::Invalid SonarQube repository inventory: ${inventory_file}"
  exit 1
fi

duplicate_repositories="$(jq -r '.repositories[].repository | ascii_downcase' "$inventory_file" | sort | uniq -d)"
if [[ -n "$duplicate_repositories" ]]; then
  echo "::error::Duplicate repositories in ${inventory_file}: ${duplicate_repositories//$'\n'/, }"
  exit 1
fi

duplicate_project_keys="$(jq -r '.repositories[].projectKey' "$inventory_file" | sort | uniq -d)"
if [[ -n "$duplicate_project_keys" ]]; then
  echo "::error::Duplicate SonarQube project keys in ${inventory_file}: ${duplicate_project_keys//$'\n'/, }"
  exit 1
fi

echo "SonarQube repository inventory is valid"
