# Panixida Core Platform

Source of truth for infrastructure automation.

This repository starts with:

- OpenTofu skeleton for Timeweb Cloud resources.
- Ansible playbooks for server bootstrap and compose deployment.
- Docker Compose stacks managed through the shared `ci-cd` deployment action.

Planned platform layers:

- Traefik + Let's Encrypt for public entrypoints.
- Keycloak for SSO.
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
- `/opt/core-platform/identity` for Keycloak.
- `/opt/core-platform/observability` for Prometheus, Grafana, Alloy, Loki, Tempo.
- `/opt/core-platform/secrets` for OpenBao or Infisical.
- `/opt/core-platform/portainer` for Portainer.
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

Production state is stored in the existing Timeweb S3-compatible bucket `db202587-tactical-heroes` at `core-platform/production.tfstate`.

Do not commit real tokens or state. In GitHub Actions, `secrets.TIMEWEB_TOKEN` is mapped to `TF_VAR_twc_token` for OpenTofu commands that need provider access.

Manual workflows:

- `OpenTofu Import Existing Infra` imports known Timeweb resources into remote state using `tofu import`, then shows drift.
- `OpenTofu Plan` runs an authoritative plan against the S3-backed state.
- `OpenTofu Apply` applies the current production configuration after an explicit confirmation string.
- `Ansible Bootstrap` applies server bootstrap through SSH.

See [docs/timeweb-inputs.md](docs/timeweb-inputs.md) for the Timeweb data needed before we model real resources.
See [docs/platform-domains.md](docs/platform-domains.md) and [docs/secrets.md](docs/secrets.md) for platform UI domains and the OpenBao bootstrap model.
