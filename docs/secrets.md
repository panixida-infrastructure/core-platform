# Secrets

OpenBao is the target central secret store for platform and service secrets.

## Bootstrap model

OpenBao has a small bootstrap problem: the server must be initialized and unsealed once before GitHub Actions can fetch service secrets from it.

The current bootstrap file is stored outside Git:

```text
C:\Users\mixai\Desktop\Infrastructure\openbao-bootstrap.json
```

The Kubernetes PostgreSQL-backed OpenBao bootstrap file is also stored outside Git:

```text
C:\Users\mixai\Desktop\Infrastructure\openbao-kubernetes-bootstrap.json
```

The repository GitHub Actions secret `OPENBAO_STATIC_SEAL_KEY` contains the 32-byte
static auto-unseal key for the Kubernetes OpenBao instance. `Kubernetes Secrets Sync`
maps it into the Kubernetes secret `secrets/openbao-static-seal`, and OpenBao reads it
through the `seal "static"` configuration block. A local copy is stored outside Git:

```text
C:\Users\mixai\Desktop\Infrastructure\openbao-static-seal-key.txt
```

The key value itself must not be written to this document or committed to Git.

This key is not an OpenBao login credential and does not replace Keycloak/OIDC access.
Keycloak authenticates humans after OpenBao is already unsealed. The static seal key is
used earlier in the lifecycle: OpenBao needs it during pod startup to decrypt its
barrier key and become operational. If this key is lost together with the Kubernetes
secret, the PostgreSQL-backed OpenBao data cannot be auto-unsealed from backup.

This repository keeps only OpenTofu state backend secrets in repository secrets:

```text
TOFU_STATE_ACCESS_KEY
TOFU_STATE_SECRET_KEY
```

`SERVER_SSH_PRIVATE_KEY` and `TIMEWEB_TOKEN` are inherited from organization secrets and should not be duplicated at repository level. Platform/service secrets, WireGuard keys, GitHub PAT, and host-specific SSH keys are stored in OpenBao.

Application/service deploy secrets are stored in OpenBao KV v2 and read by GitHub Actions through the repository OIDC token and OpenBao `jwt` auth method.

## Initial OpenBao Steps

The Kubernetes OpenBao instance uses the managed PostgreSQL backend. It has been initialized and unsealed, and `secret/core-platform/*` KV data has been copied from the legacy file-backed instance.
It was later migrated from Shamir seal to static auto-unseal.

Initial setup should be done once:

```text
1. Initialize OpenBao.
2. Store the bootstrap file, root token, and static seal key outside Git.
3. Unseal or migrate OpenBao seal as required by the current seal mode.
4. Enable KV v2 at secret/.
5. Configure Keycloak OIDC auth for human operators with `scripts/openbao/configure.sh`.
6. Configure GitHub Actions auth through OpenBao `jwt` auth.
7. Migrate known local secrets into OpenBao KV.
```

Active OpenBao secret paths:

```text
secret/core-platform/github
secret/core-platform/applications
secret/core-platform/identity
secret/core-platform/observability
secret/core-platform/openbao
secret/core-platform/sonarqube
secret/core-platform/sso
secret/core-platform/timeweb
```

The managed PostgreSQL DBaaS exporter credentials also live in `secret/core-platform/observability`:

```text
OBSERVABILITY_TIMEWEB_DBAAS_EXPORTER_ID
OBSERVABILITY_TIMEWEB_DBAAS_EXPORTER_USERNAME
OBSERVABILITY_TIMEWEB_DBAAS_EXPORTER_PASSWORD
OBSERVABILITY_TELEGRAM_BOT_TOKEN
OBSERVABILITY_WIREGUARD_CONF
```

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

Keycloak is the identity provider. Services with native OIDC support should use Keycloak directly. SonarQube Community Build uses Keycloak through SAML because native OIDC is not a supported SonarQube Community Build authentication method.

Services without native OIDC support should be protected at the Kubernetes gateway layer after the Keycloak realm and clients exist.

The `secret/core-platform/sso` path contains shared SSO bootstrap secrets that are not service-specific:

```text
HEADLAMP_OIDC_CLIENT_SECRET
```

`Kubernetes Secrets Sync` maps this value into `headlamp/headlamp-oidc` and `identity/keycloak-sso-client-secrets`. The Keycloak client id for Kubernetes API and Headlamp is `kubernetes`.
