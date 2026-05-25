# Secrets

OpenBao is the target central secret store for platform and service secrets.

## Bootstrap model

OpenBao has a small bootstrap problem: the server must be initialized and unsealed once before GitHub Actions can fetch service secrets from it.

The current bootstrap file is stored outside Git:

```text
C:\Users\mixai\Desktop\Infrastructure\openbao-bootstrap.json
```

This repository keeps only OpenTofu state backend secrets in repository secrets:

```text
TOFU_STATE_ACCESS_KEY
TOFU_STATE_SECRET_KEY
```

`SERVER_SSH_PRIVATE_KEY` and `TIMEWEB_TOKEN` are inherited from organization secrets and should not be duplicated at repository level. Platform/service secrets, WireGuard keys, GitHub PAT, and host-specific SSH keys are stored in OpenBao.

Application/service deploy secrets are stored in OpenBao KV v2 and read by GitHub Actions through the repository OIDC token and OpenBao `jwt` auth method.

## Initial OpenBao steps

The `secrets` stack starts OpenBao with persistent file storage. It will be sealed on first boot.

Initial setup should be done once:

```text
1. Initialize OpenBao.
2. Store the unseal key and root token outside Git.
3. Unseal OpenBao.
4. Enable KV v2 at secret/.
5. Configure Keycloak OIDC auth for human operators with `scripts/openbao/configure.sh`.
6. Configure GitHub Actions auth through OpenBao `jwt` auth.
7. Migrate known local secrets into OpenBao KV.
```

After that bootstrap, the `Deploy` workflow for the `secrets` stack re-applies OpenBao OIDC, JWT auth, and policies idempotently through GitHub Actions.

Current OpenBao secret paths:

```text
secret/core-platform/github
secret/core-platform/applications
secret/core-platform/identity
secret/core-platform/komodo
secret/core-platform/observability
secret/core-platform/openbao
secret/core-platform/sonarqube
secret/core-platform/ssh/infrastructure
secret/core-platform/sso
secret/core-platform/timeweb
secret/core-platform/wireguard
```

The managed PostgreSQL DBaaS exporter credentials also live in `secret/core-platform/observability`:

```text
OBSERVABILITY_TIMEWEB_DBAAS_EXPORTER_ID
OBSERVABILITY_TIMEWEB_DBAAS_EXPORTER_USERNAME
OBSERVABILITY_TIMEWEB_DBAAS_EXPORTER_PASSWORD
```

## Bootstrap variables

The bootstrap playbook reads non-secret SSH and WireGuard parameters from GitHub repository variables:

```text
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

The deployment workflows read `SERVER_HOST`, `SERVER_USER`, and `SERVER_SSH_PORT` from organization variables, not repository variables.

Managed PostgreSQL connection settings are stored in OpenBao with the service secrets:

```text
KEYCLOAK_DB_HOST
KEYCLOAK_DB_PORT
KEYCLOAK_DB_NAME
SONAR_DB_HOST
SONAR_DB_PORT
SONAR_DB_NAME
GRAFANA_DB_HOST
GRAFANA_DB_PORT
GRAFANA_DB_NAME
GRAFANA_DB_USERNAME
GRAFANA_DB_PASSWORD
OPENBAO_DB_HOST
OPENBAO_DB_PORT
OPENBAO_DB_NAME
OPENBAO_DB_USERNAME
OPENBAO_DB_PASSWORD
DOTNET_TEMPLATE_DB_HOST
DOTNET_TEMPLATE_DB_PORT
DOTNET_TEMPLATE_DB_NAME
DOTNET_TEMPLATE_DB_USERNAME
DOTNET_TEMPLATE_DB_PASSWORD
```

## SSO

Keycloak is the identity provider. Services with native OIDC support should use Keycloak directly.

Services without native OIDC support should be protected at Traefik with a forward-auth component, such as oauth2-proxy, after the Keycloak realm and clients exist.
