#!/usr/bin/env sh
set -eu

realm="${KEYCLOAK_REALM:-panixida}"
issuer="${OPENBAO_OIDC_ISSUER_URL:-https://identity.panixida.ru/realms/${realm}}"
github_audience="${OPENBAO_GITHUB_AUDIENCE:-https://github.com/panixida-infrastructure/core-platform}"
github_repository="${OPENBAO_GITHUB_REPOSITORY:-panixida-infrastructure/core-platform}"

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
EOF

if ! bao auth list -format=json | grep -q '"oidc/"'; then
  bao auth enable oidc
fi

bao write auth/oidc/config \
  oidc_discovery_url="$issuer" \
  oidc_client_id=openbao \
  oidc_client_secret="$OPENBAO_OIDC_CLIENT_SECRET" \
  default_role=platform-admin

bao write auth/oidc/role/platform-admin \
  user_claim=preferred_username \
  policies=platform-admin \
  ttl=8h \
  oidc_scopes=openid,profile,email \
  allowed_redirect_uris=https://secrets.panixida.ru/ui/vault/auth/oidc/oidc/callback \
  allowed_redirect_uris=http://localhost:8250/oidc/callback

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
