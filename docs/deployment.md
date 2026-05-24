# Deployment

The repository uses the shared deployment action from `panixida-infrastructure/ci-cd`.

## Repository variables

Set these on this repository:

```text
SERVICE_FOLDER=core-platform
```

The existing repositories appear to use organization-level values for:

```text
SERVER_USER
SERVER_HOST
SERVER_SSH_PORT
```

## Repository secrets

Set these on this repository if they are not inherited from the organization:

```text
SERVER_SSH_PRIVATE_KEY
```

## Server bootstrap

Server package/bootstrap changes go through the manual `Ansible Bootstrap` workflow. It runs `ansible/playbooks/bootstrap.yml` over SSH using the organization/repository SSH variables and secret.

The playbook currently manages only the base server shape required by compose deployments:

- Base packages.
- Docker and Compose plugin.
- Docker service state.
- Docker users.
- `/opt/core-platform`.

The initial smoke stack passes its non-secret env inline:

```text
TZ=Europe/Moscow
```

The deploy action uploads the selected stack compose file and the generated `.env` file to the server folder, then runs:

```bash
docker compose down || true
docker compose up -d --pull always
docker image prune -a -f
```

The initial workflow logs in to `ghcr.io` with the ephemeral `GITHUB_TOKEN` only because the shared action requires registry inputs. The initial stack uses public images and does not require a long-lived registry PAT.

## Multi-stack convention

Use one folder under `/opt/core-platform` per platform area:

```text
/opt/core-platform/edge
/opt/core-platform/auth
/opt/core-platform/observability
/opt/core-platform/secrets
/opt/core-platform/backups
```

Each stack gets its own compose file under `stacks/<stack>/docker-compose.yml` and should be deployed independently.
