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

## Legacy server bootstrap

The old `infrastructure` server and Docker Compose path are retained only as a rollback/migration artifact until the Kubernetes cutover is verified. Do not deploy new platform state there.

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

Kubernetes UI domains:

```text
identity.panixida.ru
secrets.panixida.ru
grafana.panixida.ru
metrics.panixida.ru
logs.panixida.ru
traces.panixida.ru
alerts.panixida.ru
argocd.panixida.ru
headlamp.panixida.ru
```

## Managed server agents

The `Ansible Bootstrap` workflow runs two playbooks:

- `bootstrap.yml` configures only the core platform server.
- `managed-agents.yml` configures every host in the `managed_servers` inventory group.

SSH keys are host-specific:

- `SERVER_SSH_PRIVATE_KEY` is used for `infrastructure`.

The managed agents stack installs `node_exporter`, `cAdvisor`, `vmagent`, and `vlagent` on every managed server. Metrics are remote-written to VictoriaMetrics and logs are remote-written to VictoriaLogs.

The central observability vmagent also scrapes the Timeweb DBaaS public exporter for the managed PostgreSQL cluster. Exporter credentials are stored in OpenBao under `secret/core-platform/observability` and are injected only into the Ansible bootstrap workflow.

## Managed PostgreSQL

OpenTofu creates the MSK-1 managed PostgreSQL cluster and private network. The manual `Managed PostgreSQL` workflow reconciles logical databases, users, automatic backups, and OpenBao connection settings.

Legacy migrations run through an SSH tunnel via the infrastructure server because the MSK DBaaS public endpoint is reachable from Timeweb but not reliably reachable from GitHub-hosted runners.

The platform uses the managed cluster for:

```text
keycloak
sonar
grafana
openbao
```

The workflow writes service connection settings to:

```text
secret/core-platform/identity
secret/core-platform/sonarqube
secret/core-platform/observability
secret/core-platform/openbao
```

## Managed Kubernetes

OpenTofu creates the Timeweb Managed Kubernetes cluster, the default worker node group, and the retained MSK-1 NVMe network drive. The manual `Kubernetes Bootstrap` workflow reads the kubeconfig from OpenTofu state, installs the first Helm-managed controllers, and applies the Argo CD root application.

The intended steady state is GitOps pull from this repository through Argo CD. The existing SSH/Docker Compose workflows remain only for migration until service data is moved from the `infrastructure` server.

The manual `Kubernetes Secrets Sync` workflow copies runtime secrets from OpenBao into Kubernetes secrets. It does not write secret values to GitHub logs or repository files. Run it after `Managed PostgreSQL` has reconciled database users and before relying on the Kubernetes workload chart.

The `platform-workloads` Argo CD application deploys the Kubernetes versions of Keycloak, OpenBao, Grafana, VictoriaMetrics, VictoriaLogs, VictoriaTraces, vmagent, vlagent, vmalert, Alertmanager, blackbox_exporter, and OpenTelemetry Collector. SonarQube remains disabled during the current migration phase.

The `Kubernetes Bootstrap` workflow installs the Timeweb network drive CSI driver with the official Helm chart after Argo CD has synced `platform-workloads`. Workloads keep persistence disabled by default during migration to avoid creating additional paid network drives accidentally. Enable chart persistence only after the retained disk/PVC cutover is planned.

Public DNS for migrated platform domains points to the Kubernetes Envoy Gateway LoadBalancer. The old Docker Compose stack can be removed after UI smoke checks pass and there is no need to retain the paused SonarQube instance.
