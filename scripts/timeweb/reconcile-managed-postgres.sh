#!/usr/bin/env bash
set -euo pipefail

timeweb_api="${TIMEWEB_API:-https://api.timeweb.cloud}"
openbao_addr="${OPENBAO_ADDR:-https://secrets.panixida.ru}"
openbao_role="${OPENBAO_ROLE:-core-platform-github-actions}"
openbao_audience="${OPENBAO_AUDIENCE:-https://github.com/panixida-infrastructure/core-platform}"

legacy_cluster_name="${LEGACY_POSTGRES_CLUSTER_NAME:-Postgres Database}"
target_cluster_name="${TARGET_POSTGRES_CLUSTER_NAME:-Postgres Database}"
target_project_id="${TARGET_POSTGRES_PROJECT_ID:-1619863}"
target_zone="${TARGET_POSTGRES_AVAILABILITY_ZONE:-msk-1}"
target_preset_id="${TARGET_POSTGRES_PRESET_ID:-1173}"
target_port="${TARGET_POSTGRES_PORT:-5432}"
target_excluded_databases="${TARGET_EXCLUDED_DATABASES:-default_db digital_event_manager}"
target_excluded_users="${TARGET_EXCLUDED_USERS:-gen_user digital_event_manager_user sonar}"
target_ssh_tunnel="${TARGET_POSTGRES_SSH_TUNNEL:-false}"
target_ssh_tunnel_port="${TARGET_POSTGRES_SSH_TUNNEL_PORT:-15432}"
target_recreate_users="${RECREATE_TARGET_DB_USERS:-}"

common_privileges='["SELECT","INSERT","UPDATE","DELETE","CREATE","TRUNCATE","REFERENCES","TRIGGER","TEMPORARY"]'
tmp_dir="${RUNNER_TEMP:-/tmp}/core-platform-postgres"

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

  if [ -n "$body" ]; then
    curl -fsS \
      -X "$method" \
      -H "Authorization: Bearer ${TIMEWEB_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "$body" \
      "${timeweb_api}${path}"
  else
    curl -fsS \
      -X "$method" \
      -H "Authorization: Bearer ${TIMEWEB_TOKEN}" \
      -H "Content-Type: application/json" \
      "${timeweb_api}${path}"
  fi
}

openbao_login() {
  require_env ACTIONS_ID_TOKEN_REQUEST_TOKEN
  require_env ACTIONS_ID_TOKEN_REQUEST_URL

  local encoded_audience oidc_response jwt login_payload
  encoded_audience="$(jq -rn --arg value "$openbao_audience" '$value|@uri')"
  oidc_response="$(curl -fsS \
    -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
    "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${encoded_audience}")"
  jwt="$(jq -r '.value' <<<"$oidc_response")"
  login_payload="$(jq -nc \
    --arg role "$openbao_role" \
    --arg jwt "$jwt" \
    '{role: $role, jwt: $jwt}')"

  curl -fsS \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$login_payload" \
    "${openbao_addr}/v1/auth/jwt/login" | jq -r '.auth.client_token'
}

bao_read() {
  local token="$1"
  local path="$2"

  curl -fsS \
    -H "X-Vault-Token: ${token}" \
    "${openbao_addr}/v1/secret/data/${path}" | jq '.data.data // {}'
}

bao_read_optional() {
  local token="$1"
  local path="$2"
  local response
  local status

  response="$(mktemp)"
  status="$(curl -sS \
    -o "$response" \
    -w '%{http_code}' \
    -H "X-Vault-Token: ${token}" \
    "${openbao_addr}/v1/secret/data/${path}")"

  if [ "$status" = "404" ]; then
    rm -f "$response"
    echo '{}'
    return
  fi

  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    cat "$response" >&2
    rm -f "$response"
    return 1
  fi

  jq '.data.data // {}' "$response"
  rm -f "$response"
}

bao_write() {
  local token="$1"
  local path="$2"
  local data="$3"

  curl -fsS \
    -X POST \
    -H "X-Vault-Token: ${token}" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --argjson data "$data" '{data: $data}')" \
    "${openbao_addr}/v1/secret/data/${path}" >/dev/null
}

cluster_by_name_zone() {
  local name="$1"
  local zone="$2"

  twc GET "/api/v1/databases?limit=100" \
    | jq -r --arg name "$name" --arg zone "$zone" \
      '.dbs[] | select(.name == $name and .availability_zone == $zone) | .id' \
    | head -n1
}

wait_cluster_started() {
  local cluster_id="$1"
  local status

  for _ in $(seq 1 80); do
    status="$(twc GET "/api/v1/databases/${cluster_id}" | jq -r '.db.status')"
    if [ "$status" = "started" ]; then
      return 0
    fi
    sleep 15
  done

  echo "::error::Timed out waiting for database cluster ${cluster_id} to start"
  exit 1
}

cluster_host() {
  local cluster_id="$1"

  twc GET "/api/v1/databases/${cluster_id}" \
    | jq -r '.db.domains[0].fqdn // (.db.networks[]? | select(.type == "public") | .ips[0].ip) // empty'
}

ensure_public_endpoint() {
  local cluster_id="$1"
  local cluster
  local public_ip_count
  local public_network_enabled

  cluster="$(twc GET "/api/v1/databases/${cluster_id}")"
  public_network_enabled="$(jq -r '.db.is_enabled_public_network // false' <<<"$cluster")"
  public_ip_count="$(jq -r '[.db.networks[]? | select(.type == "public") | .ips[]?] | length' <<<"$cluster")"

  if [ "$public_network_enabled" = "true" ] && [ "$public_ip_count" != "0" ]; then
    return
  fi

  twc PATCH "/api/v1/databases/${cluster_id}" '{"is_enabled_public_network":true}' >/dev/null
  sleep 5
}

start_target_ssh_tunnel() {
  local target_host="$1"
  local target_port="$2"
  local key_file

  if [ "$target_ssh_tunnel" != "true" ]; then
    return
  fi

  for name in SERVER_HOST SERVER_USER SERVER_SSH_PRIVATE_KEY; do
    if [ -z "${!name:-}" ]; then
      echo "::error::${name} is required when TARGET_POSTGRES_SSH_TUNNEL=true"
      exit 1
    fi
  done

  key_file="$(mktemp)"
  chmod 600 "$key_file"
  printf '%s\n' "$SERVER_SSH_PRIVATE_KEY" >"$key_file"

  echo "Opening SSH tunnel to target PostgreSQL through ${SERVER_HOST}"
  ssh \
    -f \
    -N \
    -i "$key_file" \
    -p "${SERVER_SSH_PORT:-22}" \
    -o ExitOnForwardFailure=yes \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -L "127.0.0.1:${target_ssh_tunnel_port}:${target_host}:${target_port}" \
    "${SERVER_USER}@${SERVER_HOST}"

  rm -f "$key_file"
}

ensure_instance() {
  local cluster_id="$1"
  local name="$2"
  local instance_id

  instance_id="$(twc GET "/api/v1/databases/${cluster_id}/instances" \
    | jq -r --arg name "$name" '.instances[] | select(.name == $name) | .id' \
    | head -n1)"

  if [ -n "$instance_id" ]; then
    echo "$instance_id"
    return
  fi

  twc POST "/api/v1/databases/${cluster_id}/instances" \
    "$(jq -nc --arg name "$name" '{name: $name, description: ""}')" \
    | jq -r '.instance.id'
}

ensure_user() {
  local cluster_id="$1"
  local login="$2"
  local password="$3"
  local instance_id="$4"
  local privileges="$5"
  local existing_user
  local existing_id
  local has_privileges

  existing_user="$(twc GET "/api/v1/databases/${cluster_id}/admins?limit=200" \
    | jq -c --arg login "$login" '.admins[] | select(.login == $login)' \
    | head -n1)"

  if [ -n "$existing_user" ]; then
    existing_id="$(jq -r '.id' <<<"$existing_user")"

    if should_recreate_user "$login"; then
      echo "Recreating target user ${login}"
      twc DELETE "/api/v1/databases/${cluster_id}/admins/${existing_id}" >/dev/null
      existing_user=""
    fi
  fi

  if [ -n "$existing_user" ]; then
    has_privileges="$(jq -re \
      --argjson instance_id "$instance_id" \
      --argjson required "$privileges" '
        def privilege_array:
          if type == "array" then .
          elif type == "string" then split(" ") | map(select(length > 0))
          else []
          end;

        ([.instances[]? | select(.instance_id == $instance_id) | .privileges | privilege_array][0] // []) as $actual
        | (($required - $actual) | length) == 0
      ' <<<"$existing_user" >/dev/null && echo true || echo false)"

    if [ "$has_privileges" = "true" ]; then
      echo "User ${login} already exists in target cluster with required privileges"
      return
    fi

    echo "Recreating target user ${login} with required privileges"
    twc DELETE "/api/v1/databases/${cluster_id}/admins/${existing_id}" >/dev/null
  fi

  echo "Creating target user ${login}"
  twc POST "/api/v1/databases/${cluster_id}/admins" \
    "$(jq -nc \
      --arg login "$login" \
      --arg password "$password" \
      --argjson instance_id "$instance_id" \
      --argjson privileges "$privileges" \
      '{login: $login, password: $password, host: "%", instance_id: $instance_id, privileges: $privileges, description: ""}')" \
    >/dev/null

  existing_user="$(twc GET "/api/v1/databases/${cluster_id}/admins?limit=200" \
    | jq -c --arg login "$login" '.admins[] | select(.login == $login)' \
    | head -n1)"

  if [ -z "$existing_user" ]; then
    echo "::error::Target user ${login} was not created"
    exit 1
  fi
}

should_recreate_user() {
  local login="$1"
  local recreate_login

  for recreate_login in $target_recreate_users; do
    if [ "$login" = "$recreate_login" ]; then
      return 0
    fi
  done

  return 1
}

is_excluded_database() {
  local name="$1"
  local excluded

  for excluded in $target_excluded_databases; do
    if [ "$name" = "$excluded" ]; then
      return 0
    fi
  done

  return 1
}

cleanup_excluded_target_resources() {
  local cluster_id="$1"
  local admins
  local instances
  local login
  local database_name
  local user_id
  local instance_id

  admins="$(twc GET "/api/v1/databases/${cluster_id}/admins?limit=200")"
  for login in $target_excluded_users; do
    user_id="$(jq -r --arg login "$login" '.admins[] | select(.login == $login) | .id' <<<"$admins" | head -n1)"
    if [ -n "$user_id" ]; then
      echo "Deleting excluded target user ${login}"
      twc DELETE "/api/v1/databases/${cluster_id}/admins/${user_id}" >/dev/null
    fi
  done

  instances="$(twc GET "/api/v1/databases/${cluster_id}/instances?limit=200")"
  for database_name in $target_excluded_databases; do
    instance_id="$(jq -r --arg name "$database_name" '.instances[] | select(.name == $name) | .id' <<<"$instances" | head -n1)"
    if [ -n "$instance_id" ]; then
      echo "Deleting excluded target database ${database_name}"
      twc DELETE "/api/v1/databases/${cluster_id}/instances/${instance_id}" >/dev/null
    fi
  done
}

secret_or_generate() {
  local value="$1"

  if [ -n "$value" ] && [ "$value" != "null" ]; then
    printf '%s' "$value"
    return
  fi

  printf 'Pg1%s' "$(openssl rand -hex 6)"
}

database_table_count() {
  local host="$1"
  local port="$2"
  local database="$3"
  local login="$4"
  local password="$5"

  PGPASSWORD="$password" \
  psql "host=${host} port=${port} user=${login} dbname=${database} sslmode=require" \
    -tAc "select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema');" \
    | tr -d '[:space:]'
}

migrate_database() {
  local source_host="$1"
  local source_port="$2"
  local target_host="$3"
  local target_port="$4"
  local database="$5"
  local source_login="$6"
  local source_password="$7"
  local target_login="$8"
  local target_password="$9"
  local dump_file="${tmp_dir}/${database}.dump"
  local table_count

  table_count="$(database_table_count "$target_host" "$target_port" "$database" "$target_login" "$target_password")"
  if [ "${table_count:-0}" != "0" ] && [ "${FORCE_RESTORE:-false}" != "true" ]; then
    echo "Target database ${database} is not empty, skipping restore"
    return
  fi

  echo "Migrating ${database}"
  PGPASSWORD="$source_password" \
  pg_dump "host=${source_host} port=${source_port} user=${source_login} dbname=${database} sslmode=require" \
    --format=custom \
    --no-owner \
    --no-acl \
    --file "$dump_file"

  PGPASSWORD="$target_password" \
  pg_restore \
    --dbname "host=${target_host} port=${target_port} user=${target_login} dbname=${database} sslmode=require" \
    --clean \
    --if-exists \
    --no-owner \
    --no-acl \
    "$dump_file"
}

require_env TIMEWEB_TOKEN
openbao_token="$(openbao_login)"

identity_secret="$(bao_read "$openbao_token" core-platform/identity)"
observability_secret="$(bao_read "$openbao_token" core-platform/observability)"
sonarqube_secret="$(bao_read "$openbao_token" core-platform/sonarqube)"
applications_secret="$(bao_read_optional "$openbao_token" core-platform/applications)"

keycloak_user="$(jq -r '.KEYCLOAK_DB_USERNAME' <<<"$identity_secret")"
keycloak_password="$(jq -r '.KEYCLOAK_DB_PASSWORD' <<<"$identity_secret")"
sonar_user="$(jq -r '.SONAR_DB_USERNAME' <<<"$sonarqube_secret")"
sonar_password="$(jq -r '.SONAR_DB_PASSWORD' <<<"$sonarqube_secret")"
grafana_user="$(jq -r '.GRAFANA_DB_USERNAME // "grafana_user"' <<<"$observability_secret")"
grafana_password="$(secret_or_generate "$(jq -r '.GRAFANA_DB_PASSWORD // empty' <<<"$observability_secret")")"
dotnet_template_user="$(jq -r '.DOTNET_TEMPLATE_DB_USERNAME // "dotnet_template_user"' <<<"$applications_secret")"
dotnet_template_password="$(secret_or_generate "$(jq -r '.DOTNET_TEMPLATE_DB_PASSWORD // empty' <<<"$applications_secret")")"

for name in keycloak_user keycloak_password sonar_user sonar_password grafana_user grafana_password dotnet_template_user dotnet_template_password; do
  if [ -z "${!name:-}" ] || [ "${!name}" = "null" ]; then
    echo "::error::${name} is empty"
    exit 1
  fi
done

legacy_cluster_id="$(cluster_by_name_zone "$legacy_cluster_name" spb-3 || true)"
target_cluster_id="$(cluster_by_name_zone "$target_cluster_name" "$target_zone" || true)"

if [ -z "$target_cluster_id" ]; then
  echo "::error::Target cluster '${target_cluster_name}' in ${target_zone} was not found. Run OpenTofu apply first."
  exit 1
fi

wait_cluster_started "$target_cluster_id"
ensure_public_endpoint "$target_cluster_id"
target_host="$(cluster_host "$target_cluster_id")"
if [ -z "$target_host" ]; then
  echo "::error::Target cluster ${target_cluster_id} has no public endpoint"
  exit 1
fi

echo "Ensuring target databases and users in ${target_cluster_name}"

declare -A target_users
declare -A target_passwords
declare -A target_privileges
target_users[keycloak]="$keycloak_user"
target_passwords[keycloak]="$keycloak_password"
target_privileges[keycloak]="$common_privileges"
target_users[sonar]="$sonar_user"
target_passwords[sonar]="$sonar_password"
target_privileges[sonar]="$common_privileges"
target_users[grafana]="$grafana_user"
target_passwords[grafana]="$grafana_password"
target_privileges[grafana]="$common_privileges"
target_users[dotnet_template]="$dotnet_template_user"
target_passwords[dotnet_template]="$dotnet_template_password"
target_privileges[dotnet_template]="$common_privileges"

if [ -n "$legacy_cluster_id" ]; then
  legacy_instances="$(twc GET "/api/v1/databases/${legacy_cluster_id}/instances?limit=200")"
  legacy_admins="$(twc GET "/api/v1/databases/${legacy_cluster_id}/admins?limit=200")"

  while IFS= read -r row; do
    database_name="$(jq -r '.name' <<<"$row")"
    instance_id="$(jq -r '.id' <<<"$row")"

    if is_excluded_database "$database_name"; then
      echo "Skipping excluded legacy database ${database_name}"
      continue
    fi

    admin="$(jq -rc --argjson instance_id "$instance_id" \
      '[.admins[] | select([.instances[] | select(.instance_id == $instance_id and (.privileges | length > 0))] | length > 0)][0] // empty' \
      <<<"$legacy_admins")"

    if [ -z "$admin" ]; then
      echo "No privileged legacy user found for ${database_name}, skipping user copy"
      continue
    fi

    if [ -z "${target_users[$database_name]:-}" ]; then
      target_users[$database_name]="$(jq -r '.login' <<<"$admin")"
      target_passwords[$database_name]="$(jq -r '.password' <<<"$admin")"
      target_privileges[$database_name]="$common_privileges"
    fi
  done < <(jq -rc '.instances[]' <<<"$legacy_instances")
fi

cleanup_excluded_target_resources "$target_cluster_id"

for database_name in "${!target_users[@]}"; do
  instance_id="$(ensure_instance "$target_cluster_id" "$database_name")"
  ensure_user \
    "$target_cluster_id" \
    "${target_users[$database_name]}" \
    "${target_passwords[$database_name]}" \
    "$instance_id" \
    "${target_privileges[$database_name]:-$common_privileges}"
done

backup_start_at="$(date -u +%Y-%m-%dT00:00:00Z)"
twc PATCH "/api/v1/dbs/${target_cluster_id}/auto-backups" \
  "$(jq -nc --arg creation_start_at "$backup_start_at" \
    '{is_enabled: true, copy_count: 7, creation_start_at: $creation_start_at, interval: "day", day_of_week: 1}')" \
  >/dev/null

identity_secret="$(jq \
  --arg host "$target_host" \
  --arg port "$target_port" \
  '. + {KEYCLOAK_DB_HOST: $host, KEYCLOAK_DB_PORT: $port, KEYCLOAK_DB_NAME: "keycloak"}' \
  <<<"$identity_secret")"
sonarqube_secret="$(jq \
  --arg host "$target_host" \
  --arg port "$target_port" \
  '. + {SONAR_DB_HOST: $host, SONAR_DB_PORT: $port, SONAR_DB_NAME: "sonar"}' \
  <<<"$sonarqube_secret")"
observability_secret="$(jq \
  --arg host "$target_host" \
  --arg port "$target_port" \
  --arg user "$grafana_user" \
  --arg password "$grafana_password" \
  '. + {GRAFANA_DB_HOST: $host, GRAFANA_DB_PORT: $port, GRAFANA_DB_NAME: "grafana", GRAFANA_DB_USERNAME: $user, GRAFANA_DB_PASSWORD: $password}' \
  <<<"$observability_secret")"
applications_secret="$(jq \
  --arg host "$target_host" \
  --arg port "$target_port" \
  --arg user "$dotnet_template_user" \
  --arg password "$dotnet_template_password" \
  '. + {DOTNET_TEMPLATE_DB_HOST: $host, DOTNET_TEMPLATE_DB_PORT: $port, DOTNET_TEMPLATE_DB_NAME: "dotnet_template", DOTNET_TEMPLATE_DB_USERNAME: $user, DOTNET_TEMPLATE_DB_PASSWORD: $password}' \
  <<<"$applications_secret")"

bao_write "$openbao_token" core-platform/identity "$identity_secret"
bao_write "$openbao_token" core-platform/sonarqube "$sonarqube_secret"
bao_write "$openbao_token" core-platform/observability "$observability_secret"
bao_write "$openbao_token" core-platform/applications "$applications_secret"

if [ "${MIGRATE_LEGACY_DATABASES:-false}" = "true" ] && [ -n "$legacy_cluster_id" ]; then
  mkdir -p "$tmp_dir"
  legacy_host="$(cluster_host "$legacy_cluster_id")"
  migration_target_host="$target_host"
  migration_target_port="$target_port"

  if [ -z "$legacy_host" ]; then
    echo "::error::Legacy cluster ${legacy_cluster_id} has no public endpoint"
    exit 1
  fi

  start_target_ssh_tunnel "$target_host" "$target_port"
  if [ "$target_ssh_tunnel" = "true" ]; then
    migration_target_host="127.0.0.1"
    migration_target_port="$target_ssh_tunnel_port"
  fi

  while IFS= read -r row; do
    database_name="$(jq -r '.name' <<<"$row")"
    instance_id="$(jq -r '.id' <<<"$row")"

    if is_excluded_database "$database_name"; then
      echo "Skipping migration for excluded database ${database_name}"
      continue
    fi

    admin="$(jq -rc --argjson instance_id "$instance_id" \
      '[.admins[] | select([.instances[] | select(.instance_id == $instance_id and (.privileges | length > 0))] | length > 0)][0] // empty' \
      <<<"$legacy_admins")"

    if [ -z "$admin" ] || [ -z "${target_users[$database_name]:-}" ]; then
      echo "Skipping migration for ${database_name}: missing source or target user"
      continue
    fi

    migrate_database \
      "$legacy_host" \
      "$target_port" \
      "$migration_target_host" \
      "$migration_target_port" \
      "$database_name" \
      "$(jq -r '.login' <<<"$admin")" \
      "$(jq -r '.password' <<<"$admin")" \
      "${target_users[$database_name]}" \
      "${target_passwords[$database_name]}"
  done < <(jq -rc '.instances[]' <<<"$legacy_instances")
fi

echo "Managed PostgreSQL target is reconciled: cluster=${target_cluster_id}, host=${target_host}"
