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
core-platform-quality       Dedicated worker node group for quality tools
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

OpenTofu creates the Timeweb Managed Kubernetes cluster and the default worker node group. The manual `Kubernetes Bootstrap` workflow reads the kubeconfig from OpenTofu state, installs the first Helm-managed controllers, applies the Argo CD root application, and installs the Timeweb CSI driver.

GitOps pull through Argo CD is the steady state. The `platform-workloads` Argo CD application deploys the Helm chart at:

```text
kubernetes/charts/core-platform-workloads
```

The manual `Kubernetes Secrets Sync` workflow copies runtime secrets from OpenBao into Kubernetes secrets, syncs the OpenBao static seal key from the `OPENBAO_STATIC_SEAL_KEY` GitHub secret, and reapplies OpenBao auth/SSO configuration from this repository. It does not write secret values to GitHub logs or repository files. Run it after `Managed PostgreSQL` has reconciled database users and before relying on the Kubernetes workload chart.

Platform SSO uses Keycloak as the OIDC provider. OpenTofu configures Timeweb Kubernetes OIDC for the `kubernetes` client, Argo CD is configured through the bootstrap Helm values, and the workload chart reconciles Keycloak clients for Argo CD, Kubernetes/Headlamp, Grafana, and OpenBao.

Kubernetes workloads use the public Keycloak issuer URL directly. The Timeweb LoadBalancer is configured as TCP passthrough, so TLS is terminated by Envoy Gateway with cert-manager certificates.

Public DNS for platform domains points to the Kubernetes Envoy Gateway LoadBalancer:

```text
identity.panixida.ru
secrets.panixida.ru
grafana.panixida.ru
argocd.panixida.ru
k8s.panixida.ru
sonar.panixida.ru
```

VictoriaMetrics, VictoriaLogs, VictoriaTraces, and Alertmanager are kept internal to the cluster and are consumed through Grafana, OpenTelemetry Collector, and vmalert. OpenTelemetry Collector receives application OTLP metrics/logs/traces, scrapes kubelet and cAdvisor metrics through Kubernetes service discovery, and runs HTTP endpoint checks through the `http_check` receiver. Their runtime state is stored on Timeweb NVMe network-drive PVCs created through the Kubernetes CSI storage class. Grafana dashboards are provisioned from the workload chart and cover endpoint health, Kubernetes resource usage, observability pipeline health, application OpenTelemetry metrics, logs, and traces.

Applications should send OTLP traffic to the in-cluster collector:

```text
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.observability.svc.cluster.local:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
```

If an application uses OTLP/HTTP instead of OTLP/gRPC, use port `4318` and protocol `http/protobuf`.

Kubernetes stdout/stderr logs are not tailed from every node after removing `vlagent`.
Application logs should be exported through OTLP by the application runtime.

SonarQube uses managed PostgreSQL for application data, but the Kubernetes workload is disabled while the cluster stays on the free 2 GB worker preset. Keycloak SSO for SonarQube is kept in code and uses SAML because SonarQube Community Build supports SAML with Keycloak rather than native OIDC.
