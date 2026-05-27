# Deployment

The production path is now OpenTofu for Timeweb cloud resources and Argo CD for Kubernetes workloads.

## Repository Secrets

Repository secrets should contain only OpenTofu state backend credentials:

```text
TOFU_STATE_ACCESS_KEY
TOFU_STATE_SECRET_KEY
```

`SERVER_SSH_PRIVATE_KEY` and `TIMEWEB_TOKEN` are inherited from organization secrets and should not be shadowed at repository level.

## OpenTofu

The manual `OpenTofu Apply` workflow reconciles Timeweb resources from `opentofu/envs/production`:

```text
core-platform-network       Timeweb VPC in MSK-1
core-platform               Timeweb Managed Kubernetes cluster in MSK-1
core-platform-default       Default worker node group
core-platform-nvme          Retained NVMe network drive in MSK-1
postgres                    Managed PostgreSQL cluster in MSK-1
panixida-storage            S3 bucket for OpenTofu state and platform storage
panixida.ru DNS records     Platform UI records pointing to Envoy Gateway
```

The retired `infrastructure` VM, its floating IP, SSH key, Ansible bootstrap, and Docker Compose deployments are no longer part of the desired state.

## Managed PostgreSQL

OpenTofu creates the MSK-1 managed PostgreSQL cluster and private network. The manual `Managed PostgreSQL` workflow reconciles logical databases, users, automatic backups, and OpenBao connection settings.

The platform uses the managed cluster for:

```text
keycloak
sonar
grafana
openbao
dotnet_template
```

The workflow writes service connection settings to:

```text
secret/core-platform/identity
secret/core-platform/sonarqube
secret/core-platform/observability
secret/core-platform/openbao
secret/core-platform/applications
```

## Managed Kubernetes

OpenTofu creates the Timeweb Managed Kubernetes cluster, the default worker node group, and the retained MSK-1 NVMe network drive. The manual `Kubernetes Bootstrap` workflow reads the kubeconfig from OpenTofu state, installs the first Helm-managed controllers, applies the Argo CD root application, installs the Timeweb CSI driver, and applies the retained network drive PV/PVC.

GitOps pull through Argo CD is the steady state. The `platform-workloads` Argo CD application deploys the Helm chart at:

```text
kubernetes/charts/core-platform-workloads
```

The manual `Kubernetes Secrets Sync` workflow copies runtime secrets from OpenBao into Kubernetes secrets. It does not write secret values to GitHub logs or repository files. Run it after `Managed PostgreSQL` has reconciled database users and before relying on the Kubernetes workload chart.

Public DNS for platform domains points to the Kubernetes Envoy Gateway LoadBalancer:

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

SonarQube remains disabled during the current migration phase. Its managed PostgreSQL database/user may stay reconciled, but `sonar.panixida.ru` is intentionally not published until the workload is re-enabled.
