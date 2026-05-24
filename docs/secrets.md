# Secrets

OpenBao is the target central secret store for platform and service secrets.

## Bootstrap model

OpenBao has a small bootstrap problem: GitHub Actions needs a few secrets before it can fetch the rest from OpenBao.

For now, GitHub keeps only deployment/bootstrap secrets:

```text
TOFU_STATE_ACCESS_KEY
TOFU_STATE_SECRET_KEY
KEYCLOAK_DB_USERNAME
KEYCLOAK_DB_PASSWORD
KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME
KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD
WIREGUARD_PRIVATE_KEY
WIREGUARD_PRESHARED_KEY
KOMODO_DATABASE_USERNAME
KOMODO_DATABASE_PASSWORD
KOMODO_INIT_ADMIN_USERNAME
KOMODO_INIT_ADMIN_PASSWORD
KOMODO_WEBHOOK_SECRET
KOMODO_JWT_SECRET
KOMODO_OIDC_CLIENT_SECRET
SERVER_GH_PAT
```

`SERVER_SSH_PRIVATE_KEY` and `TIMEWEB_TOKEN` are inherited from organization secrets and should not be duplicated at repository level.

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

## Bootstrap variables

The bootstrap playbook reads non-secret SSH and WireGuard parameters from GitHub repository variables:

```text
SERVER_HOST
SERVER_USER
SERVER_SSH_PORT
SERVER_GH_USER
CODEXVPN_SSH_PUBLIC_KEY
ROOT_SSH_PUBLIC_KEY
WIREGUARD_ADDRESS
WIREGUARD_PEER_PUBLIC_KEY
WIREGUARD_ENDPOINT
WIREGUARD_ALLOWED_IPS
WIREGUARD_PERSISTENT_KEEPALIVE
```

WireGuard private and preshared keys must stay in GitHub Secrets until OpenBao is initialized and CI can authenticate to it.

`SERVER_GH_PAT` is used only by the Ansible bootstrap workflow to install and authenticate GitHub CLI on the infrastructure server for `root` and the operator user.

## SSO

Keycloak is the identity provider. Services with native OIDC support should use Keycloak directly.

Services without native OIDC support should be protected at Traefik with a forward-auth component, such as oauth2-proxy, after the Keycloak realm and clients exist.
