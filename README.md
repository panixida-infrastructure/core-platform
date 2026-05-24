# Panixida Core Platform

Source of truth for infrastructure automation.

This repository starts with:

- OpenTofu skeleton for Timeweb Cloud resources.
- Ansible playbooks for server bootstrap and compose deployment.
- Docker Compose stacks managed through the shared `ci-cd` deployment action.

Planned platform layers:

- Traefik + Let's Encrypt for public entrypoints.
- authentik for SSO.
- Prometheus, Grafana, Alloy, Loki, Tempo for observability.
- OpenBao or Infisical for secrets.
- Harbor plus BaGetter or Nexus for registries/packages.
- Restic backups with restore checks.
- Trivy, SonarQube, CodeQL in CI.
- K3s later, if Docker Compose stops being enough.

## Layout

```text
ansible/              Server configuration and deployment playbooks
docs/                 Operator notes and required provider inputs
opentofu/envs/        OpenTofu environments
stacks/               Docker Compose source of truth by platform stack
```

## Stack Layout

Stacks should deploy under one repository-owned namespace:

```text
/opt/core-platform/<stack>
```

Examples:

- `/opt/core-platform/edge` for Traefik and public entrypoints.
- `/opt/core-platform/auth` for authentik.
- `/opt/core-platform/observability` for Prometheus, Grafana, Alloy, Loki, Tempo.
- `/opt/core-platform/secrets` for OpenBao or Infisical.
- `/opt/core-platform/backups` for Restic jobs.

This keeps blast radius small: deploying one stack should not restart unrelated platform tools.

## CI deploy

`.github/workflows/cd.yml` uses:

```text
PANiXiDA-Infrastructure/ci-cd/.github/actions/ssh-docker-compose-deploy@main
```

Repository-specific values:

- `SERVICE_FOLDER=core-platform`

Shared organization values are expected to provide SSH and registry settings, the same way as the existing `portainer`, `keycloak`, and `nginx-proxy-manager` repositories.

## OpenTofu

OpenTofu is wired for the Timeweb Cloud provider:

```hcl
source = "tf.timeweb.cloud/timeweb-cloud/timeweb-cloud"
```

Do not commit real tokens or state. In GitHub Actions, map `secrets.TIMEWEB_TOKEN` to `TF_VAR_twc_token` for OpenTofu commands that need provider access.

See [docs/timeweb-inputs.md](docs/timeweb-inputs.md) for the Timeweb data needed before we model real resources.
