# Kubernetes Migration

The migration is split into two safe phases.

## Phase 1: Create the managed cluster

OpenTofu owns cloud resources:

```text
core-platform-network       Timeweb VPC in MSK-1
core-platform               Timeweb Managed Kubernetes cluster in MSK-1
core-platform-default       Default worker node group
core-platform-nvme          Retained NVMe network drive in MSK-1
postgres                    Managed PostgreSQL cluster in MSK-1
panixida-storage            S3 bucket for OpenTofu state and platform storage
```

The first apply must not delete the existing `infrastructure` server. It remains the source of local Docker volumes until service data is migrated and Kubernetes UIs are verified.

Cluster defaults:

```text
Kubernetes version: v1.35.4+k0s.0
Master preset:      2947, Promo MSK
Worker preset:      2951, Promo MSK 2 CPU / 2 GB / 40 GB
Worker autoscaling: 4-6 nodes
CNI:                cilium
Built-in ingress:   disabled
```

Worker public IPs are enabled for the first cluster creation because Timeweb requires every worker group to use either public IPs or a virtual router. The target production hardening step is to add a Timeweb virtual router and then disable public IPs on workers.

Labels are lightweight key/value metadata attached to nodes. They are used by Kubernetes scheduling, selectors, and operational grouping. The default node group receives `panixida.ru/node-pool=core-platform`.

Taints repel pods unless pods explicitly tolerate them. No taints are configured initially because this is a single general-purpose node pool and all platform workloads must be schedulable.

The cluster OIDC provider is not configured at creation time. Keycloak is one of the workloads being migrated into the cluster, so cluster-level OIDC has to be added after the Keycloak realm/client exists and its issuer URL is stable.

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

Docker Compose deployments and Ansible server bootstrap stay only as a temporary migration path until all services have been moved.

Kubernetes workload secrets are not stored in Git. Run the manual `Kubernetes Secrets Sync` workflow before or immediately after enabling the workload chart. It reads OpenBao through GitHub Actions OIDC and applies only Kubernetes `Secret` objects for:

```text
identity/keycloak-secrets
secrets/openbao-secrets
observability/grafana-secrets
observability/observability-secrets
quality/sonarqube-secrets
```

Planned workload replacements:

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

SonarQube migration is intentionally paused. The managed PostgreSQL database and user can stay reconciled, but the Kubernetes workload is disabled until the cluster has enough stable capacity for Elasticsearch.

During migration Keycloak runs as a single replica with `KC_CACHE=local`. This avoids JDBC/JGroups discovery against the old Docker Keycloak instance that still shares the same managed PostgreSQL database. Switch back to distributed cache only after the old instance is stopped and the Kubernetes replica topology is finalized.

OpenBao gets a dedicated managed PostgreSQL database and user named `openbao` / `openbao_user`. The Kubernetes OpenBao instance has been initialized and unsealed on the PostgreSQL backend, and current KV data has been copied from the legacy file-backed OpenBao before switching traffic.

The Kubernetes OpenBao workload uses the managed PostgreSQL backend from the first start. Keep bootstrap material outside Git and do not cut `secrets.panixida.ru` over until the Timeweb LoadBalancer TLS issue is resolved.

The workload chart currently exposes migrated UIs through the shared HTTP listener only. HTTPS cutover for the old platform domains should be done after the Timeweb LoadBalancer TLS passthrough issue is resolved and DNS is intentionally repointed to the Kubernetes LoadBalancer IP.

## Storage

For Timeweb Managed Kubernetes, persistent workloads should use the Timeweb network drive CSI storage classes:

```text
nvme.network-drives.csi.timeweb.cloud
hdd.network-drives.csi.timeweb.cloud
```

The OpenTofu-managed `core-platform-nvme` disk is declared as a retained static PV/PVC by the Kubernetes bootstrap workflow. It is not attached to the old VM server. The bootstrap workflow installs the Timeweb CSI driver with Helm after the GitOps workloads are synced and stale pending PVCs from the first migration attempt are removed.

## Cutover rules

Do not remove the `infrastructure` server until all of these are true:

```text
1. Kubernetes cluster is active.
2. Envoy Gateway has a public LoadBalancer IP.
3. DNS records are intentionally repointed to that IP.
4. Keycloak, OpenBao, Grafana, and observability UIs are reachable through Envoy Gateway. SonarQube is excluded while its migration is paused.
5. OpenBao data has been migrated from file storage to PostgreSQL and unseal/bootstrap material is verified outside Git.
6. Local Docker volumes that still contain unique data have been migrated or explicitly discarded.
```

After the DNS cutover, the old `infrastructure` server is not required for platform UI traffic. It can be destroyed through OpenTofu once the migrated domains pass smoke checks.
