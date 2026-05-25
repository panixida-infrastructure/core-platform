# Deployment

The repository uses the shared deployment action from `panixida-infrastructure/ci-cd`.

## Repository variables

Set these on this repository:

```text
SERVICE_FOLDER=core-platform
TRAEFIK_ACME_EMAIL=<lets-encrypt-account-email>
```

The existing repositories appear to use organization-level values for:

```text
SERVER_USER
SERVER_HOST
SERVER_SSH_PORT
SERVER_GH_USER
```

## Organization secrets

These secrets are inherited from the organization and should not be shadowed at repository level:

```text
SERVER_SSH_PRIVATE_KEY
TIMEWEB_TOKEN
```

## Repository secrets

Repository secrets should contain only OpenTofu state backend credentials:

```text
TOFU_STATE_ACCESS_KEY
TOFU_STATE_SECRET_KEY
```

## Server bootstrap

Server package/bootstrap changes go through the manual `Ansible Bootstrap` workflow. It runs `ansible/playbooks/bootstrap.yml` over SSH using organization SSH variables and the organization `SERVER_SSH_PRIVATE_KEY` secret. Runtime bootstrap secrets are read from OpenBao through GitHub Actions OIDC.

The playbook currently manages only the base server shape required by compose deployments:

- Base packages.
- Docker and Compose plugin.
- Docker service state.
- Docker users.
- `/opt/core-platform`.
- The shared Docker network `core-platform-edge`.

The deploy action uploads the selected stack compose file and the generated `.env` file to the server folder, then runs:

```bash
docker compose down || true
docker compose up -d --pull always
docker image prune -a -f
```

The deploy workflow logs in to `ghcr.io` with the ephemeral `GITHUB_TOKEN` only because the shared action requires registry inputs. Current platform stacks use public images and do not require a long-lived registry PAT.

The `komodo` stack reads its local database, initial admin, and OIDC secrets from OpenBao.

The `identity` stack starts Keycloak in production mode and uses the PostgreSQL default `public` schema.

## Multi-stack convention

Use one folder under `/opt/core-platform` per platform area:

```text
/opt/core-platform/edge
/opt/core-platform/identity
/opt/core-platform/observability
/opt/core-platform/secrets
/opt/core-platform/komodo
/opt/core-platform/sonarqube
```

Each stack gets its own compose file under `stacks/<stack>/docker-compose.yml` and should be deployed independently.

Current UI domains:

```text
traefik.panixida.ru
identity.panixida.ru
secrets.panixida.ru
komodo.panixida.ru
auth.panixida.ru
grafana.panixida.ru
metrics.panixida.ru
logs.panixida.ru
traces.panixida.ru
alerts.panixida.ru
sonar.panixida.ru
```

## Managed server agents

The `Ansible Bootstrap` workflow runs two playbooks:

- `bootstrap.yml` configures only the core platform server.
- `managed-agents.yml` configures every host in the `managed_servers` inventory group.

SSH keys are host-specific:

- `SERVER_SSH_PRIVATE_KEY` is used only for `infrastructure`.
- `TACTICALHEROES_DEV_SSH_PRIVATE_KEY` is used only for `TacticalHeroes.Dev`.

The managed agents stack installs `node_exporter`, `cAdvisor`, `vmagent`, and `vlagent` on every managed server. Metrics are remote-written to VictoriaMetrics and logs are remote-written to VictoriaLogs.

The central observability vmagent also scrapes the Timeweb DBaaS public exporter for the managed PostgreSQL cluster. Exporter credentials are stored in OpenBao under `secret/core-platform/observability` and are injected only into the Ansible bootstrap workflow.
