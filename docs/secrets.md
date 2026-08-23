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
secret/core-platform/github-provisioner
secret/core-platform/applications
secret/core-platform/identity
secret/core-platform/observability
secret/core-platform/openbao
secret/core-platform/sonarqube
secret/core-platform/sso
secret/core-platform/timeweb
```

The SonarQube path stores the persistent session key and the GitHub App credentials used for repository import:

```text
SONAR_AUTH_JWTBASE64HS256SECRET
SONAR_GITHUB_APP_ID
SONAR_GITHUB_CLIENT_ID
SONAR_GITHUB_CLIENT_SECRET
SONAR_GITHUB_PRIVATE_KEY
```

`SONAR_AUTH_JWTBASE64HS256SECRET` keeps SonarQube user sessions valid across pod restarts. The Kubernetes secrets sync creates it once when missing and stores it in OpenBao. External Secrets Operator then synchronizes the complete `core-platform/sonarqube` path into `quality/sonarqube-secrets` through a namespace-scoped read-only OpenBao role.

GitHub App registrations are one-time account-owner bootstraps because GitHub requires interactive ownership and installation approval. App registration metadata, names, manifests, permissions, and installation state are not managed from this repository. Store generated credentials directly in OpenBao. For the SonarQube App, enable **Allow wildcard matching** for the `https://sonar.panixida.ru/` callback URL. SonarQube appends the project creation path and query parameters to this callback. The `Kubernetes Secrets Sync` workflow then syncs the Kubernetes secret and forces an Argo CD reconciliation so the SonarQube PostSync job creates or updates the GitHub integration.

The repository provisioner credential contract is stored at `secret/core-platform/github-provisioner`:

```text
GITHUB_APP_ID
GITHUB_APP_PRIVATE_KEY
```

The `SonarQube Repositories Sync` workflow reads this path and `secret/core-platform/sonarqube` through GitHub OIDC. It reconciles only repositories declared in `inventory/sonarqube/repositories.json`; it does not create or modify GitHub App registrations.

If Kubernetes API access is unavailable, run the `SonarQube GitHub Sync` workflow with confirmation `sync-sonarqube-github`. It authenticates to OpenBao with GitHub OIDC, reads only `core-platform/sonarqube`, and idempotently creates or updates the global GitHub integration through the public SonarQube API. No SonarQube credentials are stored in GitHub secrets or workflow artifacts.

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
TACTICAL_HEROES_DEV_DB_NAME
TACTICAL_HEROES_DEV_DB_USERNAME
TACTICAL_HEROES_DEV_DB_PASSWORD
TACTICAL_HEROES_PROD_DB_NAME
TACTICAL_HEROES_PROD_DB_USERNAME
TACTICAL_HEROES_PROD_DB_PASSWORD
TACTICAL_HEROES_DEV_CLIENT_SECRET
TACTICAL_HEROES_PROD_CLIENT_SECRET
TACTICAL_HEROES_SMTP_PASSWORD
```

`Tactical Heroes Mail` creates or updates the Timeweb mailbox and writes the complete SMTP overlay to both application paths:

```text
secret/applications/tactical-heroes-api/development
secret/applications/tactical-heroes-api/production
```

External Secrets Operator extracts each complete path into the environment secret, so individual keys do not need to be listed in the application manifest.

## SSO

Keycloak is the identity provider. Services with native OIDC support should use Keycloak directly. SonarQube Community Build uses Keycloak through SAML because native OIDC is not a supported SonarQube Community Build authentication method.

Services without native OIDC support should be protected at the Kubernetes gateway layer after the Keycloak realm and clients exist.

The `secret/core-platform/sso` path contains shared SSO bootstrap secrets that are not service-specific:

```text
HEADLAMP_OIDC_CLIENT_SECRET
```

`Kubernetes Secrets Sync` maps this value into `headlamp/headlamp-oidc` and `identity/keycloak-sso-client-secrets`. The Keycloak client id for Kubernetes API and Headlamp is `kubernetes`.
