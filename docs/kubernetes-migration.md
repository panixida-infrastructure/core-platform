# Kubernetes Migration

The migration is split into two safe phases.

## Phase 1: Create the managed cluster

OpenTofu owns cloud resources:

```text
core-platform-network       Timeweb VPC in MSK-1
core-platform-router        Timeweb virtual router for Kubernetes worker egress in MSK-1
core-platform               Timeweb Managed Kubernetes cluster in MSK-1
core-platform-default       Default worker node group
postgres                    Managed PostgreSQL cluster in MSK-1
panixida-storage            S3 bucket for OpenTofu state and platform storage
```

The legacy `infrastructure` server is retired after the DNS cutover. It is no longer part of the desired OpenTofu state.

Cluster defaults:

```text
Kubernetes version: v1.35.4+k0s.0
Master preset:      2947, Promo MSK
Worker preset:      2951, Promo MSK 2 CPU / 2 GB / 40 GB
Worker autoscaling: 1-6 nodes
Worker networking:  private worker IPs with egress through Timeweb virtual router
CNI:                cilium
Built-in ingress:   disabled
```

Worker public IPs were enabled for the first cluster creation because Timeweb requires every worker group to use either public IPs or a virtual router. The production target is private worker IPs with outbound internet access through a Timeweb virtual router. Public platform traffic still enters through the Envoy Gateway LoadBalancer.

Labels are lightweight key/value metadata attached to nodes. They are used by Kubernetes scheduling, selectors, and operational grouping. The default node group receives `panixida.ru/node-pool=core-platform`.

Taints repel pods unless pods explicitly tolerate them. The default node group has no taints because general platform workloads must be schedulable there.

The cluster OIDC provider is managed by OpenTofu after Keycloak is reachable at `identity.panixida.ru`. Kubernetes trusts the Keycloak `panixida` realm, uses the `kubernetes` client, maps usernames from `preferred_username`, and maps RBAC groups from `groups`.

## Phase 2: Move workloads

GitHub Actions bootstraps only the Kubernetes control plane tooling:

```text
cert-manager
Envoy Gateway
External Secrets Operator
Argo CD
Headlamp
```

After Argo CD is available, workload deployment is pull-based from this repository. The root Argo CD application applies the shared platform resources and creates the `platform-workloads` child application from the local Helm chart at:

```text
kubernetes/charts/core-platform-workloads
```

Application deployment follows the same GitOps model. The root application currently creates `dotnet-template-development`, which pulls the Helm chart from `panixida-templates/dotnet-backend-template` and tracks the `development` branch. The production DNS record and Gateway certificate are prepared, but `dotnet-template-production` is enabled only after the Helm chart is merged into `dotnet-template/main`.

Application runtime secrets are synchronized by External Secrets Operator from OpenBao. The application chart creates namespace-scoped SecretStores and ExternalSecrets; Git only stores the remote OpenBao paths and never stores secret values.

Docker Compose deployments and Ansible server bootstrap have been removed from the desired state. Workload deployment is pull-based through Argo CD.

Keycloak clients and groups for platform SSO are reconciled by the workload chart through a PostSync job. Argo CD uses the `argocd` public client with PKCE. Kargo uses the `kargo` public client with PKCE. Grafana and OpenBao use their native OIDC clients. Headlamp uses the `kubernetes` client, which is also the audience configured on the Timeweb Kubernetes OIDC provider.

Kubernetes workload secrets are not stored in Git. Run the manual `Kubernetes Secrets Sync` workflow before or immediately after enabling the workload chart. It reads OpenBao through GitHub Actions OIDC and applies only Kubernetes `Secret` objects for:

```text
identity/keycloak-secrets
identity/keycloak-sso-client-secrets
secrets/openbao-secrets
observability/grafana-secrets
observability/observability-secrets
headlamp/headlamp-oidc
quality/sonarqube-secrets
```

Application secrets are read by External Secrets Operator from:

```text
secret/applications/dotnet-template/development
secret/applications/dotnet-template/production
secret/applications/dotnet-template/registry
```

The registry path is written by the application repository CI through an OpenBao JWT role scoped only to that path.

Workload replacements:

```text
Traefik       -> Envoy Gateway
Komodo        -> Argo CD + Headlamp
oauth2-proxy  -> remove where native OIDC exists; only keep a replacement if a UI has no native OIDC
OpenBao       -> Kubernetes workload with PostgreSQL storage
Keycloak      -> Kubernetes workload with managed PostgreSQL
Grafana       -> Kubernetes workload with managed PostgreSQL
SonarQube     -> Kubernetes workload with managed PostgreSQL
Victoria*     -> Kubernetes workloads with retained PVCs
```

SonarQube has Kubernetes manifests and a managed PostgreSQL database, but the workload is currently disabled because the free 2 GB worker preset is too constrained for a reliable SonarQube deployment.

During migration Keycloak runs as a single replica with `KC_CACHE=local`. This avoids JDBC/JGroups discovery against the old Docker Keycloak instance that still shares the same managed PostgreSQL database. Switch back to distributed cache only after the old instance is stopped and the Kubernetes replica topology is finalized.

OpenBao gets a dedicated managed PostgreSQL database and user named `openbao` / `openbao_user`. The Kubernetes OpenBao instance has been initialized on the PostgreSQL backend, migrated to static auto-unseal, and current KV data has been copied from the legacy file-backed OpenBao before switching traffic.

The Kubernetes OpenBao workload uses the managed PostgreSQL backend from the first start. Keep bootstrap material outside Git.

The workload chart exposes migrated UIs through the shared HTTP listener and per-host HTTPS listeners. The Timeweb LoadBalancer is configured as TCP passthrough, so public HTTPS traffic reaches Envoy Gateway and uses the cert-manager certificates.

VictoriaMetrics, VictoriaLogs, VictoriaTraces, and Alertmanager are intentionally not exposed through Gateway routes or public DNS. Grafana uses their internal Kubernetes service DNS names as datasources and provisions dashboards from Git for endpoint health, Kubernetes resource usage, observability pipeline health, application logs, traces, and application OpenTelemetry metrics. OpenTelemetry Collector is the single telemetry collection layer: applications push OTLP metrics/logs/traces to it, while Kubernetes kubelet/cAdvisor and platform endpoint checks are still collected by Collector receivers. Victoria agent workloads (`vmagent`, `vlagent`) and `blackbox-exporter` are not deployed. These stores use Timeweb NVMe network-drive PVCs because their local TSDB/log/traces data should survive pod and node replacement.

## Storage

For Timeweb Managed Kubernetes, persistent workloads should use the Timeweb network drive CSI storage classes:

```text
nvme.network-drives.csi.timeweb.cloud
hdd.network-drives.csi.timeweb.cloud
```

Persistent platform workloads use per-workload Timeweb NVMe PVCs provisioned dynamically by the Kubernetes CSI driver. The bootstrap workflow installs the Timeweb CSI driver with Helm after the GitOps workloads are synced and stale pending PVCs from the first migration attempt are removed.

## Cutover rules

The `infrastructure` server can stay destroyed when all of these remain true:

```text
1. Kubernetes cluster is active.
2. Envoy Gateway has a public LoadBalancer IP.
3. DNS records point to that IP.
4. Keycloak, OpenBao, Grafana, Argo CD, and Headlamp are reachable through Envoy Gateway. Raw observability endpoints stay internal and are consumed through Grafana. SonarQube is intentionally disabled until the cluster has a larger worker preset.
5. OpenBao data has been migrated from file storage to PostgreSQL and unseal/bootstrap material is verified outside Git.
6. Local Docker volumes that still contain unique data have been migrated or explicitly discarded.
```
