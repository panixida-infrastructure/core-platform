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
core-platform-infrastructure Infrastructure worker node group with public worker IPv4 for registry/API egress
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
dotnet_template_dev
dotnet_template_prod
telegram_alert_gateway
```

The workflow writes service connection settings to:

```text
secret/core-platform/identity
secret/core-platform/sonarqube
secret/core-platform/observability
secret/core-platform/openbao
secret/core-platform/applications
secret/core-platform/telegram-alert-gateway
```

The manual `Tactical Heroes Mail` workflow reconciles the single paid `tactical-heroes@panixida.ru` mailbox through the Timeweb API. It preserves the mailbox password in `secret/core-platform/applications` and merges SMTP settings into both Tactical Heroes application paths in OpenBao. The current Timeweb provider has no mail resource, and keeping this operation outside OpenTofu also prevents the mailbox password from entering its state.

## Managed Kubernetes

OpenTofu creates the Timeweb Managed Kubernetes cluster and one infrastructure worker node group. Workers currently use public IPv4 for reliable registry and Timeweb API egress; public application traffic still enters through the Envoy Gateway LoadBalancer. The manual `Kubernetes Bootstrap` workflow reads the kubeconfig from OpenTofu state, installs the first Helm-managed controllers, applies the Argo CD root application, and installs the Timeweb CSI driver.

The bootstrap also pins the Timeweb-managed Cilium agent and operator to the direct Kubernetes API endpoint from kubeconfig. This prevents a Cilium restart from depending on the unavailable in-cluster API service route while the node network is still initializing.

GitOps pull through Argo CD is the steady state. The `platform-workloads` Argo CD application deploys the Helm chart at:

```text
kubernetes/charts/core-platform-workloads
```

Application deployment is also pull-based through Argo CD. The root `core-platform` application creates development and production child applications:

```text
dotnet-template-development   development branch dev.api.dotnet-template.panixida.ru
dotnet-template-production    main branch        api.dotnet-template.panixida.ru
tactical-heroes-development  development branch dev.api.tactical-heroes.panixida.ru
tactical-heroes-production   main branch        api.tactical-heroes.panixida.ru
telegram-alert-gateway       main branch        internal observability service
```

The application chart lives in the application repository at `deploy/helm/dotnet-template`. GitHub Actions builds API and EF migrator images. Kargo watches `development-*` image tags for the development stage and `production-*` image tags for the production stage, writes promoted tags to the matching `images-*.yaml` file, and asks Argo CD to sync the target application.

Application runtime secrets are pulled by External Secrets Operator from OpenBao. The chart creates a namespace-scoped `SecretStore` that authenticates through OpenBao Kubernetes auth and syncs `dotnet-template-api-env` from:

```text
secret/applications/dotnet-template/development
secret/applications/dotnet-template/production
```

Development uses `dotnet_template_dev` with `dotnet_template_user_dev`. Production uses `dotnet_template_prod` with `dotnet_template_user_prod`. The chart also expects registry pull credentials in:

```text
secret/applications/dotnet-template/registry
```

The application repository CI updates this registry path through the `dotnet-template-github-actions` OpenBao JWT role. That role is scoped only to registry pull credentials and cannot read application database secrets.

Tactical Heroes follows the same flow with values under `deploy/helm/tactical-heroes-api`, Kargo project `tactical-heroes`, OpenBao paths under `secret/applications/tactical-heroes-api`, and separate `tactical_heroes_dev` and `tactical_heroes_prod` databases.

The manual `Kubernetes Secrets Sync` workflow copies runtime secrets from OpenBao into Kubernetes secrets, syncs the OpenBao static seal key from the `OPENBAO_STATIC_SEAL_KEY` GitHub secret, and reapplies OpenBao auth/SSO configuration from this repository. It does not write secret values to GitHub logs or repository files. Run it after `Managed PostgreSQL` has reconciled database users and before relying on the Kubernetes workload chart.

Platform SSO uses Keycloak as the OIDC provider. OpenTofu configures Timeweb Kubernetes OIDC for the `kubernetes` client, Argo CD is configured through the bootstrap Helm values, and the workload chart reconciles Keycloak clients for Argo CD, Kubernetes/Headlamp, Grafana, OpenBao, and Kargo.

Kubernetes workloads use the public Keycloak issuer URL directly. The Timeweb LoadBalancer is configured as TCP passthrough, so TLS is terminated by Envoy Gateway with cert-manager certificates.

Public DNS for platform domains points to the Kubernetes Envoy Gateway LoadBalancer:

```text
identity.panixida.ru
secrets.panixida.ru
grafana.panixida.ru
argocd.panixida.ru
k8s.panixida.ru
kargo.panixida.ru
sonar.panixida.ru
api.dotnet-template.panixida.ru
dev.api.dotnet-template.panixida.ru
api.tactical-heroes.panixida.ru
dev.api.tactical-heroes.panixida.ru
```

VictoriaMetrics, VictoriaLogs, VictoriaTraces, and Alertmanager are kept internal to the cluster and are consumed through Grafana, OpenTelemetry Collector, and vmalert. OpenTelemetry Collector receives application OTLP metrics/logs/traces, scrapes kubelet and cAdvisor metrics through Kubernetes service discovery, and runs HTTP endpoint checks through the `http_check` receiver. An OpenTelemetry `filelog` DaemonSet also tails Kubernetes container stdout/stderr on every node and stores offsets on the node so application and infrastructure logs remain available in VictoriaLogs across collector restarts. Their runtime state is stored on Timeweb NVMe network-drive PVCs created through the Kubernetes CSI storage class. Grafana dashboards are provisioned from the workload chart and cover endpoint health, Kubernetes resource usage, observability pipeline health, application OpenTelemetry metrics, logs, and traces.

Alertmanager groups metric and health alerts by owner/environment and posts authenticated webhooks to `telegram-alert-gateway`. The gateway persists notifications in PostgreSQL, keeps `firing`/`resolved`, paginates long groups without dropping alerts, and routes each alert to exactly one owner forum topic: `tactical-heroes`, `dotnet-template`, `postgresql`, `core-platform`, or `observability`. Unknown owners use `unclassified`; only synthetic checks with an explicit owner use `unclassified-tests`.

The gateway also polls completed one-minute VictoriaLogs windows and sends each distinct error as a self-contained log notification; repeated copies of the same normalized error in one window are collapsed into an occurrence count. For OTLP-enabled API containers, the filelog collector skips the duplicate stdout copy while Kubernetes logs remain available through `kubectl logs`. Containers without direct OTLP log export continue to be collected from stdout/stderr.

Telegram delivery uses the official .NET client. It prefers the WireGuard-backed `telegram-vpn` HTTP proxy and falls back to direct egress for transport and gateway failures. Alertmanager retains a narrow native Telegram emergency receiver only for gateway/downstream delivery alerts, so a gateway outage can still be reported. Synthetic checks monitor the VPN and final egress routes, and vmalert watches gateway readiness, gateway delivery failures, and Alertmanager webhook failures.

Linux host metrics for Kubernetes worker nodes are collected by a `node-exporter` DaemonSet and scraped by OpenTelemetry Collector with the `linux-node-exporter` job. Kubelet `/metrics` remains enabled for Kubernetes node/kubelet metrics, while kubelet cAdvisor remains the source for pod and container CPU, memory, filesystem, and network usage.

Managed PostgreSQL metrics are collected from the Timeweb DBaaS Prometheus exporter. OpenTelemetry Collector scrapes both the PostgreSQL exporter endpoint for database metrics and the DB host `node_exporter` endpoint for server metrics, then stores them in VictoriaMetrics. The exporter id and basic-auth credentials live in the OpenBao `secret/core-platform/observability` path and are synced into the `observability/observability-secrets` Kubernetes secret.

Kubernetes workloads connect to Managed PostgreSQL through its private VPC address. The public floating IP remains available only for external administration and database migration, so replacing that IP does not require changing workload connection strings.

Applications should send OTLP traffic to the in-cluster collector:

```text
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.observability.svc.cluster.local:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
```

If an application uses OTLP/HTTP instead of OTLP/gRPC, use port `4318` and protocol `http/protobuf`.

Kubernetes stdout/stderr logs are tailed on every node by the OpenTelemetry filelog DaemonSet. Applications with direct OTLP log export are excluded from duplicate filelog ingestion per container; applications without OTLP continue to use filelog as their VictoriaLogs source.

SonarQube uses managed PostgreSQL for application data. Keycloak SSO for SonarQube uses SAML because SonarQube Community Build supports SAML with Keycloak rather than native OIDC.
