# Panixida Infrastructure

Source of truth for infrastructure automation.

This repository starts with:

- OpenTofu skeleton for Timeweb Cloud resources.
- Ansible playbooks for server bootstrap and compose deployment.
- Docker Compose stack managed through the shared `ci-cd` deployment action.

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
compose/              Docker Compose source of truth for the infra stack
docs/                 Operator notes and required provider inputs
opentofu/envs/        OpenTofu environments
scripts/              Local helper entrypoints
```

## Local bootstrap on the current server

```bash
cd /home/codexvpn/infra
sudo apt-get update
sudo apt-get install -y ansible
ansible-playbook -i ansible/inventories/production/hosts.yml ansible/playbooks/bootstrap.yml
ansible-playbook -i ansible/inventories/production/hosts.yml ansible/playbooks/deploy-compose.yml
```

## CI deploy

`.github/workflows/cd.yml` uses:

```text
PANiXiDA-Infrastructure/ci-cd/.github/actions/ssh-docker-compose-deploy@main
```

Repository-specific values:

- `SERVICE_FOLDER=infra`
- `COMPOSE_FILE=compose/docker-compose.yml`
- `ENV_FILE` secret with compose environment content

Shared organization values are expected to provide SSH and registry settings, the same way as the existing `portainer`, `keycloak`, and `nginx-proxy-manager` repositories.

## OpenTofu

OpenTofu is wired for the Timeweb Cloud provider:

```hcl
source = "tf.timeweb.cloud/timeweb-cloud/timeweb-cloud"
```

Do not commit real tokens or state. Use `TWC_TOKEN` locally or GitHub secrets in CI.

See [docs/timeweb-inputs.md](docs/timeweb-inputs.md) for the Timeweb data needed before we model real resources.
