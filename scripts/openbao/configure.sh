#!/usr/bin/env sh
set -eu

realm="${KEYCLOAK_REALM:-panixida}"
issuer="${OPENBAO_OIDC_ISSUER_URL:-https://identity.panixida.ru/realms/${realm}}"
github_audience="${OPENBAO_GITHUB_AUDIENCE:-https://github.com/panixida-infrastructure/core-platform}"
github_repository="${OPENBAO_GITHUB_REPOSITORY:-panixida-infrastructure/core-platform}"
dotnet_template_github_audience="${OPENBAO_DOTNET_TEMPLATE_GITHUB_AUDIENCE:-https://github.com/panixida-templates/dotnet-backend-template}"
dotnet_template_github_repository="${OPENBAO_DOTNET_TEMPLATE_GITHUB_REPOSITORY:-panixida-templates/dotnet-backend-template}"
tactical_heroes_github_audience="${OPENBAO_TACTICAL_HEROES_GITHUB_AUDIENCE:-https://github.com/tactical-heroes/api}"
tactical_heroes_github_repository="${OPENBAO_TACTICAL_HEROES_GITHUB_REPOSITORY:-tactical-heroes/api}"
kubernetes_auth_path="${OPENBAO_KUBERNETES_AUTH_PATH:-kubernetes}"

if [ -z "${OPENBAO_OIDC_CLIENT_SECRET:-}" ]; then
  echo "OPENBAO_OIDC_CLIENT_SECRET is required" >&2
  exit 1
fi

if ! bao secrets list -format=json | grep -q '"secret/"'; then
  bao secrets enable -path=secret -version=2 kv
fi

bao policy write platform-admin - <<'EOF'
path "*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
EOF

bao policy write github-actions - <<'EOF'
path "secret/data/core-platform/*" {
  capabilities = ["read"]
}

path "secret/metadata/core-platform/*" {
  capabilities = ["read", "list"]
}

path "secret/data/core-platform/identity" {
  capabilities = ["create", "read", "update"]
}

path "secret/data/core-platform/observability" {
  capabilities = ["create", "read", "update"]
}

path "secret/data/core-platform/sonarqube" {
  capabilities = ["create", "read", "update"]
}

path "secret/data/core-platform/openbao" {
  capabilities = ["create", "read", "update"]
}

path "secret/data/core-platform/sso" {
  capabilities = ["create", "read", "update"]
}

path "secret/data/core-platform/applications" {
  capabilities = ["create", "read", "update"]
}

path "secret/data/applications/dotnet-template/*" {
  capabilities = ["create", "read", "update"]
}

path "secret/metadata/applications/dotnet-template/*" {
  capabilities = ["read", "list"]
}

path "secret/data/applications/tactical-heroes-api/*" {
  capabilities = ["create", "read", "update"]
}

path "secret/metadata/applications/tactical-heroes-api/*" {
  capabilities = ["read", "list"]
}

path "secret/data/applications/tactical-heroes-admin/*" {
  capabilities = ["create", "read", "update"]
}

path "secret/metadata/applications/tactical-heroes-admin/*" {
  capabilities = ["read", "list"]
}

path "sys/mounts" {
  capabilities = ["read", "list"]
}

path "sys/mounts/secret" {
  capabilities = ["create", "read", "update", "sudo"]
}

path "sys/auth" {
  capabilities = ["read", "list"]
}

path "sys/auth/oidc" {
  capabilities = ["create", "read", "update", "sudo"]
}

path "sys/auth/jwt" {
  capabilities = ["create", "read", "update", "sudo"]
}

path "sys/auth/kubernetes" {
  capabilities = ["create", "read", "update", "sudo"]
}

path "auth/oidc/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

path "auth/jwt/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

path "auth/kubernetes/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

path "sys/policies/acl" {
  capabilities = ["read", "list"]
}

path "sys/policies/acl/platform-admin" {
  capabilities = ["create", "read", "update"]
}

path "sys/policies/acl/github-actions" {
  capabilities = ["create", "read", "update"]
}

path "sys/policies/acl/sonarqube-app" {
  capabilities = ["create", "read", "update"]
}

path "sys/policies/acl/dotnet-template-app" {
  capabilities = ["create", "read", "update"]
}

path "sys/policies/acl/dotnet-template-github-actions" {
  capabilities = ["create", "read", "update"]
}

path "sys/policies/acl/tactical-heroes-api-app" {
  capabilities = ["create", "read", "update"]
}

path "sys/policies/acl/tactical-heroes-admin-app" {
  capabilities = ["create", "read", "update"]
}

path "sys/policies/acl/tactical-heroes-api-github-actions" {
  capabilities = ["create", "read", "update"]
}
EOF

bao policy write sonarqube-app - <<'EOF'
path "secret/data/core-platform/sonarqube" {
  capabilities = ["read"]
}

path "secret/metadata/core-platform/sonarqube" {
  capabilities = ["read"]
}
EOF

bao policy write dotnet-template-app - <<'EOF'
path "secret/data/applications/dotnet-template/*" {
  capabilities = ["read"]
}

path "secret/metadata/applications/dotnet-template/*" {
  capabilities = ["read", "list"]
}
EOF

bao policy write dotnet-template-github-actions - <<'EOF'
path "secret/data/applications/dotnet-template/registry" {
  capabilities = ["create", "read", "update"]
}

path "secret/metadata/applications/dotnet-template/registry" {
  capabilities = ["read", "list"]
}
EOF

bao policy write tactical-heroes-api-app - <<'EOF'
path "secret/data/applications/tactical-heroes-api/*" {
  capabilities = ["read"]
}

path "secret/metadata/applications/tactical-heroes-api/*" {
  capabilities = ["read", "list"]
}
EOF

bao policy write tactical-heroes-admin-app - <<'EOF'
path "secret/data/applications/tactical-heroes-admin/*" {
  capabilities = ["read"]
}

path "secret/metadata/applications/tactical-heroes-admin/*" {
  capabilities = ["read", "list"]
}
EOF

bao policy write tactical-heroes-api-github-actions - <<'EOF'
path "secret/data/applications/tactical-heroes-api/registry" {
  capabilities = ["create", "read", "update"]
}

path "secret/metadata/applications/tactical-heroes-api/registry" {
  capabilities = ["read", "list"]
}
EOF

if ! bao auth list -format=json | grep -q '"oidc/"'; then
  bao auth enable oidc
fi

bao write auth/oidc/config \
  oidc_discovery_url="$issuer" \
  oidc_client_id=openbao \
  oidc_client_secret="$OPENBAO_OIDC_CLIENT_SECRET" \
  default_role=platform-admin

cat >/tmp/openbao-platform-admin-role.json <<'EOF'
{
  "role_type": "oidc",
  "user_claim": "preferred_username",
  "groups_claim": "groups",
  "bound_claims": {
    "groups": ["platform-admins"]
  },
  "policies": ["platform-admin"],
  "ttl": "8h",
  "oidc_scopes": ["openid", "profile", "email"],
  "allowed_redirect_uris": [
    "https://secrets.panixida.ru/ui/vault/auth/oidc/oidc/callback",
    "http://localhost:8250/oidc/callback"
  ]
}
EOF

bao write auth/oidc/role/platform-admin @/tmp/openbao-platform-admin-role.json
rm -f /tmp/openbao-platform-admin-role.json

if ! bao auth list -format=json | grep -q '"jwt/"'; then
  bao auth enable jwt
fi

bao write auth/jwt/config \
  oidc_discovery_url=https://token.actions.githubusercontent.com \
  bound_issuer=https://token.actions.githubusercontent.com

cat >/tmp/core-platform-github-actions-role.json <<EOF
{
  "role_type": "jwt",
  "user_claim": "repository",
  "bound_audiences": ["${github_audience}"],
  "bound_claims": {
    "repository": "${github_repository}"
  },
  "policies": ["github-actions"],
  "ttl": "15m"
}
EOF

bao write auth/jwt/role/core-platform-github-actions @/tmp/core-platform-github-actions-role.json
rm -f /tmp/core-platform-github-actions-role.json

cat >/tmp/dotnet-template-github-actions-role.json <<EOF
{
  "role_type": "jwt",
  "user_claim": "repository",
  "bound_audiences": ["${dotnet_template_github_audience}"],
  "bound_claims": {
    "repository": "${dotnet_template_github_repository}"
  },
  "policies": ["dotnet-template-github-actions"],
  "ttl": "15m"
}
EOF

bao write auth/jwt/role/dotnet-template-github-actions @/tmp/dotnet-template-github-actions-role.json
rm -f /tmp/dotnet-template-github-actions-role.json

cat >/tmp/tactical-heroes-api-github-actions-role.json <<EOF
{
  "role_type": "jwt",
  "user_claim": "repository",
  "bound_audiences": ["${tactical_heroes_github_audience}"],
  "bound_claims": {
    "repository": "${tactical_heroes_github_repository}"
  },
  "policies": ["tactical-heroes-api-github-actions"],
  "ttl": "15m"
}
EOF

bao write auth/jwt/role/tactical-heroes-api-github-actions @/tmp/tactical-heroes-api-github-actions-role.json
rm -f /tmp/tactical-heroes-api-github-actions-role.json

if ! bao auth list -format=json | grep -q "\"${kubernetes_auth_path}/\""; then
  bao auth enable -path="$kubernetes_auth_path" kubernetes
fi

bao write "auth/${kubernetes_auth_path}/config" \
  kubernetes_host=https://kubernetes.default.svc:443

bao write "auth/${kubernetes_auth_path}/role/sonarqube" \
  bound_service_account_names=sonarqube-external-secrets \
  bound_service_account_namespaces=quality \
  policies=sonarqube-app \
  ttl=1h

bao write "auth/${kubernetes_auth_path}/role/dotnet-template-development" \
  bound_service_account_names=dotnet-template-external-secrets \
  bound_service_account_namespaces=dotnet-template-development \
  policies=dotnet-template-app \
  ttl=1h

bao write "auth/${kubernetes_auth_path}/role/dotnet-template-production" \
  bound_service_account_names=dotnet-template-external-secrets \
  bound_service_account_namespaces=dotnet-template-production \
  policies=dotnet-template-app \
  ttl=1h

bao write "auth/${kubernetes_auth_path}/role/tactical-heroes-api-development" \
  bound_service_account_names=tactical-heroes-api-external-secrets \
  bound_service_account_namespaces=tactical-heroes-development \
  policies=tactical-heroes-api-app \
  ttl=1h

bao write "auth/${kubernetes_auth_path}/role/tactical-heroes-api-production" \
  bound_service_account_names=tactical-heroes-api-external-secrets \
  bound_service_account_namespaces=tactical-heroes-production \
  policies=tactical-heroes-api-app \
  ttl=1h

bao write "auth/${kubernetes_auth_path}/role/tactical-heroes-admin-development" \
  bound_service_account_names=tactical-heroes-admin-external-secrets \
  bound_service_account_namespaces=tactical-heroes-admin-development \
  policies=tactical-heroes-admin-app \
  ttl=1h

bao write "auth/${kubernetes_auth_path}/role/tactical-heroes-admin-production" \
  bound_service_account_names=tactical-heroes-admin-external-secrets \
  bound_service_account_namespaces=tactical-heroes-admin-production \
  policies=tactical-heroes-admin-app \
  ttl=1h
