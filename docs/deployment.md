# Deployment

The repository uses the shared deployment action from `panixida-infrastructure/ci-cd`.

## Repository variables

Set these on this repository:

```text
SERVICE_FOLDER=infra
COMPOSE_FILE=compose/docker-compose.yml
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
ENV_FILE
```

For the initial stack, `ENV_FILE` can be:

```text
TZ=Europe/Moscow
```

The deploy action uploads `compose/docker-compose.yml` and the generated `.env` file to the server folder, then runs:

```bash
docker compose down || true
docker compose up -d --pull always
docker image prune -a -f
```

The initial workflow logs in to `ghcr.io` with the ephemeral `GITHUB_TOKEN` only because the shared action requires registry inputs. The initial stack uses public images and does not require a long-lived registry PAT.
