# Secrets

OpenBao is the target central secret store for platform and service secrets.

## Bootstrap model

OpenBao has a small bootstrap problem: GitHub Actions needs a few secrets before it can fetch the rest from OpenBao.

For now, GitHub keeps only deployment/bootstrap secrets:

```text
SERVER_SSH_PRIVATE_KEY
TIMEWEB_TOKEN
TOFU_STATE_ACCESS_KEY
TOFU_STATE_SECRET_KEY
KEYCLOAK_DB_USERNAME
KEYCLOAK_DB_PASSWORD
KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME
KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD
```

After OpenBao is initialized, move application and infrastructure secrets into OpenBao KV and leave GitHub with only the minimum credentials needed to authenticate to OpenBao.

## Initial OpenBao steps

The `secrets` stack starts OpenBao with persistent file storage. It will be sealed on first boot.

Initial setup should be done once:

```text
1. Initialize OpenBao.
2. Store the unseal key and root token outside Git.
3. Unseal OpenBao.
4. Enable KV v2 at secret/.
5. Configure Keycloak OIDC auth for human operators.
6. Configure GitHub Actions auth, preferably via OIDC/JWT, for CI secret reads.
7. Migrate known local secrets into OpenBao.
```

GitHub secret values cannot be read back from GitHub after they are created. Only secrets that still exist in local `.secrets/` files, or are re-entered by an operator, can be migrated into OpenBao.

## SSO

Keycloak is the identity provider. Services with native OIDC support should use Keycloak directly.

Services without native OIDC support should be protected at Traefik with a forward-auth component, such as oauth2-proxy, after the Keycloak realm and clients exist.
