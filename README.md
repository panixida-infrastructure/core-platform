# Panixida Core Platform

Source of truth for infrastructure automation.

This repository starts with:

- OpenTofu skeleton for Timeweb Cloud resources.
- Timeweb Managed Kubernetes provisioning.
- A temporary Ansible and Docker Compose migration path for the old `infrastructure` server.
- GitOps bootstrap for Kubernetes through Helm, Envoy Gateway, Argo CD, and Headlamp.

Planned platform layers:

- Envoy Gateway for public entrypoints.
- Keycloak for SSO.
- Grafana, VictoriaMetrics, VictoriaLogs, VictoriaTraces, vmalert, Alertmanager, node-exporter, OpenTelemetry Collector, and the owner-routed Telegram alert gateway for observability.
- OpenBao for secrets.
- Argo CD and Headlamp for Kubernetes management.
- Restic backups with restore checks.
- Trivy, SonarQube, CodeQL in CI.

## Layout

```text
ansible/              Server configuration and deployment playbooks
docs/                 Operator notes and required provider inputs
kubernetes/           Kubernetes bootstrap and GitOps source
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
- `/opt/core-platform/observability` for Grafana and the VictoriaMetrics observability stack.
- `/opt/core-platform/secrets` for OpenBao.
- `/opt/core-platform/komodo` for Komodo.
- `/opt/core-platform/sonarqube` for SonarQube.
- `/opt/core-platform/backups` for Restic jobs.

This keeps blast radius small: deploying one stack should not restart unrelated platform tools.

## CI deploy

`.github/workflows/cd.yml` uses:

```text
PANiXiDA-Infrastructure/ci-cd/.github/actions/ssh-docker-compose-deploy@main
```

Repository-specific values:

- `SERVICE_FOLDER=core-platform`

Shared organization values are expected to provide SSH and registry settings, the same way as the existing platform repositories.

## OpenTofu

OpenTofu is wired for the Timeweb Cloud provider:

```hcl
source = "tf.timeweb.cloud/timeweb-cloud/timeweb-cloud"
```

Production state is stored in the Timeweb S3-compatible bucket `panixida-storage` at `core-platform/production.tfstate`.

Managed PostgreSQL automatic backups run daily and retain one copy. Both OpenTofu and the `Managed PostgreSQL` workflow enforce this retention.

Managed Valkey 9.1 is declared in `opentofu/envs/production/valkey.tf`: one node in
`core-platform-network` (`msk-1`), preset `5067` (1 CPU, 2 GiB RAM, 10 GiB disk,
1700 RUB/month as of 2026-10-29). Public IPv4/IPv6, automatic backups, and paid
deletion recovery are disabled. `noeviction` protects coordination/session keys
from cache eviction; callers must handle memory-limit errors and use bounded TTLs.
The instance is shared initially and does not provide replication or failover.
Timeweb restricts administrative Valkey commands, including `CONFIG GET`. Change
parameters through OpenTofu/provider API; the workflow verifies the configuration
through that API and requires CONFIG and ACL enumeration to remain unavailable
to application users.

`OpenTofu Apply` checks the provider settings, stores `VALKEY_HOST`, `VALKEY_PORT`,
`VALKEY_USERNAME`, and `VALKEY_PASSWORD` in the corresponding OpenBao paths
`secret/applications/tactical-heroes-api/development` and `/production`.
OpenTofu owns `tactical_heroes_user_dev` and `tactical_heroes_user_prod` with
independent random passwords and CONNECTION, READ, WRITE, PUBSUB, SCRIPTING,
and TRANSACTION privileges. Credentials remain in restricted state and OpenBao.
Temporary client pods on every ready Kubernetes worker verify each user's
identity, strings, hashes, TTL, Lua, transactions, Pub/Sub, and denials for
CONFIG and ACL enumeration. Timeweb category grants overlap: WRITE permits
FLUSHALL, FLUSHDB, and SWAPDB even without DANGEROUS. This provider behavior is
accepted; these commands are not invoked or required to be denied by the probes.

After both application users pass the basic cache/state checks, the workflow removes the bootstrap
`default` account through Timeweb, verify rejection of its old password and
unauthenticated access, and remove its current shared OpenBao credentials.
The create-only cluster admin block remains to avoid replacing the service.
Temporary pods and Secrets are always cleaned up. Application deployments are
not changed; application integration is a separate step. Separate users do not
isolate dev/prod keys or Pub/Sub channels: the provider exposes category grants,
not key/channel ACL patterns. Applications must use environment-specific prefixes.
Pub/Sub message delivery and CONFIG/ACL denial checks run after bootstrap
retirement and remain required for successful verification.

`envoy_gateway_user` is a separate identity for the shared Envoy RateLimit service,
with CONNECTION, READ, WRITE, and SCRIPTING privileges. The pinned
[RateLimit implementation](https://github.com/envoyproxy/ratelimit/blob/0482748e/src/redis/fixed_cache_impl.go)
uses GET, INCRBY/EXPIRE pipelines, and Lua for counter refunds; it does not need
PUBSUB or TRANSACTION. Its independent password and the same four VALKEY fields
are published to `secret/core-platform/envoy-gateway`. Reconcile the GitHub Actions
policy from `scripts/openbao/configure.sh` before the first publication to this path.
Worker probes verify RESP2/RESP3 authentication, counter operations, TTL and Lua
refunds, and rejection of PUBLISH, MULTI, CONFIG and ACL enumeration before the
application Pub/Sub checks. The same accepted WRITE-category behavior applies to
this user; separate users do not enforce key-prefix isolation. Gateway rate
limiting and its permanent Kubernetes secret integration are configured separately.

Run `python3 -B -m unittest discover -s tests -p test_managed_valkey.py` and
`tofu -chdir=opentofu/envs/production validate` to check the Valkey automation.

The Timeweb service and host exporters feed the existing Collector → VictoriaMetrics
pipeline. Grafana provisions **Managed Valkey (shared)** with instance-wide metrics;
vmalert routes capacity, availability, persistence and collection alerts to the
`core-platform` Telegram topic. See [the Valkey monitoring runbook](docs/valkey-monitoring.md)
for credentials, thresholds, source limitations and verification.
To deploy, merge the configuration and run `OpenTofu Apply` with
`confirm=apply-production`. Re-running it also repeats the connectivity checks.
Upgrading the tariff restarts the managed service; do not rotate the create-only
admin password or change the engine type in place (replacement is protected).

The `timeweb-postgres-backups` collector reads the Timeweb physical backup inventory and automatic backup settings every 15 minutes. It uses the existing observability Timeweb credential through External Secrets and performs GET requests only. It does not create, delete, restore, or reschedule backups. Configuration is under `observability.postgresBackups` in the workloads chart.

The **Timeweb backups** section of Grafana's [Managed PostgreSQL](https://grafana.panixida.ru/d/postgresql-managed/postgresql-managed) dashboard shows automatic and manual copies separately, their creation dates and ages, provider statuses, retention, and API polling health. It is also linked from Core Platform Overview. Only copies currently in `done` state count as successful; their age is based on the provider's `created_at`, not a completion timestamp or a restore test.

Backup alerts route to the `postgresql` Telegram topic:

- Automatic backups disabled for 10 minutes: warning.
- No successful automatic backup within 36 hours, for 30 minutes: warning. A manual copy does not reset this check; when no completed copy of either type exists, the critical alert below takes precedence.
- No completed automatic or manual copy for 10 minutes: critical.
- The latest attempt of either type is in `fail` state for 15 minutes: warning. An older failed copy does not keep this alert active after a newer attempt.
- No successful API poll for an hour, or missing collector metrics, for 5 minutes: warning. Failed or stale polls suppress backup-state alerts rather than turning unknown state into missing copies.

Local validation: `python3 -B -m unittest discover -s tests`, `helm lint kubernetes/charts/core-platform-workloads`, then render the chart and run `tests/verify_backup_alerts.py` and `tests/verify_metric_links.py` against the rendered YAML (these two checks require Docker). Validate runs the same checks in CI.

Do not commit real tokens or state. In GitHub Actions, `secrets.TIMEWEB_TOKEN` is mapped to `TF_VAR_twc_token` for OpenTofu commands that need provider access.

Manual workflows:

- `OpenTofu Import Existing Infra` imports known Timeweb resources into remote state using `tofu import`, then shows drift.
- `OpenTofu Plan` runs an authoritative plan against the S3-backed state.
- `OpenTofu Apply` applies the current production configuration after an explicit confirmation string.
- `Kubernetes Bootstrap` installs the first Kubernetes controllers and applies the Argo CD root app.
- `Kubernetes Secrets Sync` copies OpenBao-backed runtime secrets into Kubernetes `Secret` objects for the migrated workloads.
- `Ansible Bootstrap` applies server bootstrap through SSH.

The managed master uses the Base preset (`1675`: 4 CPU, 8 GiB RAM, 60 GiB disk).
Timeweb provider 1.7.2 reports a successful `twc_k8s_cluster.preset_id` update
without changing the running master. Therefore `OpenTofu Apply` also reconciles
the preset from its saved plan through the dedicated `/master-nodes` API and
waits for the cluster and master resources to match. The reconciliation refuses
resource reductions, availability-zone changes, and changes to the master count.
When applying locally, run `opentofu/scripts/reconcile-k8s-master-preset.sh`
with the cluster ID and the applied plan's master preset ID, using `TIMEWEB_TOKEN`.
A successful provider apply alone is insufficient verification of a resize.

See [docs/timeweb-inputs.md](docs/timeweb-inputs.md) for the Timeweb data needed before we model real resources.
See [docs/kubernetes-migration.md](docs/kubernetes-migration.md) for the Kubernetes migration plan and cutover rules.
See [docs/platform-domains.md](docs/platform-domains.md) and [docs/secrets.md](docs/secrets.md) for platform UI domains and the OpenBao bootstrap model.

## Background work monitoring

[Application Telemetry](https://grafana.panixida.ru/d/application-telemetry/application-telemetry)
includes shared sections for Wolverine inbox/outbox, DLQ arrivals/inventory/message age,
delayed messages, recurring commands and Quartz jobs. The service selector also discovers
workers without HTTP request metrics. Queue inventory uses `max` across replicas sharing
the same store; counter increases and active Quartz jobs are summed across instances.

`IScheduler` commands/events and published Wolverine recurring occurrences share the
durable scheduled inbox. Future messages do not count as overdue. The recurring section
shows durable pause state and delay past the recorded next occurrence, not business-job
success. Quartz is a separate scheduler: its section shows executions, failures, mean/p95
duration, active jobs and misfires. Misfire/error counters may be absent before the first
event; zero fallbacks are anchored to an observed scheduler/job, never a global zero.

Defaults under `observability.backgroundWork`:

- Inbox/outbox: more than 100 messages for 10 minutes.
- DLQ: new arrivals during the last 15 minutes, more than 10 remaining for 5 minutes,
  or oldest message age over 1 hour for 10 minutes.
- Delayed messages/recurring schedules: overdue by more than 5 minutes for 5 minutes.
- Quartz: errors or misfires in the last 15 minutes, sustained for 1 minute; p95 over
  60 seconds for 15 minutes with at least five completed executions per window.

Thresholds are starting operational limits and can be tuned in Helm values. Quartz duration
does not detect a job that never finishes. DLQ age is measured from original `sent_at`,
not DLQ insertion. New Core persistence gauges require the messaging-library release;
without it their panels remain empty and inventory/age/overdue alerts remain inactive.
Quartz p95 evaluates only instances with the improved seconds histogram (the 0.001 bucket),
avoiding mixed layouts during rollout. No telemetry freshness alerts are added.

Alerts use existing application owner/environment routing and link to their panel.
For Tactical Heroes this is its Telegram topic (955). Validate with Helm lint/render,
`python3 -B tests/verify_background_work.py <rendered-manifests.yaml>` and
`python3 -B tests/verify_metric_links.py <rendered-manifests.yaml>` (Docker required).

## Container restarts and failures

The **Container restarts and failures** section of [Kubernetes Workloads](https://grafana.panixida.ru/d/kubernetes-workloads/kubernetes-workloads) shows regular and init container restarts, CrashLoopBackOff, abnormal terminations, and state collection health. kube-state-metrics reads only Pods (`list`/`watch`) and exposes an allowlist of container lifecycle metrics to the existing OpenTelemetry/VictoriaMetrics pipeline.

- `KubernetesContainerRestartsHigh`: at least 3 observed restarts in 15 minutes, sustained for 2 minutes. A new Pod UID or a previously accumulated counter does not count as new restarts.
- `KubernetesContainerCrashLoopBackOff`: recurring CrashLoopBackOff over a five-minute observation window, sustained for 10 minutes (critical).
- `KubernetesContainerTerminatedAbnormally`: a current failed termination or a non-zero exit within 10 minutes, sustained for 1 minute. The reason includes OOMKilled/Error; Completed containers and Pods being deleted are excluded. Retained failed Job Pods (including SonarQube PR cleanup) resolve 10 minutes after their recorded completion. New failures, active Jobs and failed service containers still alert; missing completion data does not suppress an alert.
- `KubernetesContainerStateMonitoringDown`: exporter scrape or container inventory absent for 5 minutes.

Application namespaces route to their existing Tactical Heroes or .NET Template Telegram topics; `observability` routes to Observability and other system namespaces to Core Platform. The alert links retain namespace, Pod, and container filters. History is collected from deployment onward.

## Managed control-plane log collection

The Timeweb collector accepts native klog timestamps and provider renderings with `GMT`,
`GMT+0000` or an explicit numeric offset. It polls all four control-plane services
independently, so a failed service does not block the others. Errors include only a safe
failure reason, operation, node ID and scope; provider bodies and exception text stay private.

Each page is exported before its cursor is saved in `timeweb-control-plane-log-state`.
After at most 20 pages per service in a cycle, backfill continues from that cursor on
the next cycle or after a restart. The previous completed watermark and overlap counts
remain intact until the scan reaches its original cutoff. This bounds memory to a page
and the overlap checkpoint instead of buffering the entire backlog. Argo CD preserves
the runtime ConfigMap data during sync and uses server-side apply for this resource,
so checkpoints are not copied into the size-limited last-applied annotation.

Delivery is at least once: an ambiguous export/checkpoint failure may replay the last
page, but never advances past unconfirmed records. Source timestamps are retained during
backfill; neither the state nor the cutoff is reset to skip history. Recovery depends
on the history still being available from Timeweb within the seven-day query window.
Unknown timestamps, malformed responses, non-advancing cursors and an oversized checkpoint
fail explicitly without advancing that page. Rejected cursors restart from the original
cutoff, potentially replaying confirmed pages instead of skipping the remaining backlog.
`timeweb_control_plane_logs_pending_scans` counts unfinished
scans; `ControlPlaneLogCollectionStalled` clears after a fully successful polling cycle.

Validate with `python3 -B -m unittest discover -s tests`, Helm lint/render and
`python3 -B tests/verify_container_alerts.py <rendered-manifests.yaml>` (Docker required).

## Infrastructure release monitoring

The **Infrastructure updates** section of [Core Platform Overview](https://grafana.panixida.ru/d/core-platform-overview/core-platform-overview?viewPanel=10) shows installed and available versions, source links, compatibility notes and the last successful release check. The collector reads observed Pod image tags and Helm labels every five minutes. Public stable release metadata is checked every 24 hours, with hourly retries after a failed check. Errors retain the last successful result but display a failed status.

The catalog is `kubernetes/charts/core-platform-workloads/files/infrastructure-releases.json`. It covers the platform services, observability stack, Helm charts and helper images. Kubernetes and managed PostgreSQL use Timeweb's supported version catalogs. Provider add-ons are displayed as a bundle; own application builds use their existing delivery pipelines. An unknown image in a configured infrastructure namespace appears as `untracked` and raises a warning after 30 minutes. Add its official source to the catalog instead of treating it as current.

`InfrastructureUpdateAvailable` is informational and goes to **Core Platform (960)** through Alertmanager's native Telegram receiver and the existing Telegram egress proxy. It includes both versions, an official source link and a dashboard link. The collector stores the highest announced version on a dedicated 1 GiB PVC; daily checks, restarts and application rollbacks do not repeat it. The notification event lasts one hour, the route repeats only after 24 hours, and resolved messages are disabled: expiration of a notification does not mean an upgrade happened. A notification-pipeline outage longer than the event window can miss a message; the dashboard still retains the available version. Deleting the PVC resets this history.

`InfrastructureReleaseCheckFailed` reports a failed source, unreadable installed version or an untracked image after 30 minutes. `InfrastructureReleaseMonitorDown` reports missing/stale inventory or a state-write failure after 10 minutes. Existing Telegram delivery-failure monitoring covers the native receiver.

Availability is not compatibility approval: SonarQube Community Build requires checking the custom branch plugin and database upgrade path; Redis and Envoy must stay compatible with their parent charts/controllers. Image variants and pinned digests must be reviewed when updating. This monitor neither installs updates nor scans CVEs or host OS packages. It has Kubernetes `list` access only to Pods and Deployments, and uses the existing Timeweb credential only for GET requests.

Validate with `python3 -B -m unittest discover -s tests`, `helm lint kubernetes/charts/core-platform-workloads`, then render the chart and run `python3 -B tests/verify_release_alerts.py <rendered-manifests.yaml>` (Docker required).

## Browser telemetry ingestion

Browser OTLP traffic enters through an application's authenticated BFF, then the
Collector's internal `otlp/frontend` HTTP receiver on port 4319. This is a
ClusterIP service port; no public HTTPRoute is created. The existing server OTLP
receivers on 4317/4318 are unchanged.

The frontend receiver body limit is configured by
`observability.frontendTelemetry.maxRequestBodySize` (1048576 bytes / 1 MiB by default).
Keep the client exporter limit <= BFF body limit <= this Collector limit. The .NET
OpenTelemetry 1.19.1 exporter requires at least 750000 bytes, so a 64 KiB limit is
not compatible with its request-size setting. Before batching or exporting,
`resource/frontend` removes client-supplied service/environment identities and
sets them from the BFF's `X-Frontend-Service` and `X-Frontend-Environment` headers.
It sets `service.namespace=browser` and `deployment.environment.name` from the BFF.
The BFF must assign these headers itself and
must not pass through copies supplied by the browser. Browser metrics, logs and
traces then use the existing VictoriaMetrics, VictoriaLogs and VictoriaTraces
exporters.

Collectors normalize legacy `deployment.environment` to `deployment.environment.name`,
preserving an existing canonical value and removing the legacy key before export.

Deploy this receiver before enabling the admin's browser exports. A local
validation with Docker and a rendered chart is available:

```bash
python3 -B tests/verify_frontend_telemetry.py /tmp/core-platform-workloads.yaml
```

The check uses the Collector image pinned by the chart, submits all three OTLP
signals with forged resource identities, and verifies that the exported resource
identities were overwritten from the BFF metadata.

## Blazor dashboards and alerts

`Application Telemetry` includes server circuits and Blazor processing times;
`Browser Telemetry` shows WASM navigation, handlers, rendering, HTTP, and linked logs/traces.
Application views exclude browser resources; Browser Telemetry orders filters as Service,
Environment, Runtime instance. Blazor panels show observed operation counts alongside p95:
idle counters can be zero while duration remains undefined until operations occur.
Filter by environment and runtime instance. Reporting runtimes are not unique users;
missing reports from closed or sleeping tabs are normal. Runtime GUIDs increase metric
series cardinality; the browser dashboard shows recently reporting series.

`Observability Pipeline` filters collectors, receivers (including `otlp/frontend`), and exporters.
Delivery failures, sustained queue pressure and repeated component errors are alerted.
Thresholds and HTTP minimum traffic are configured under `observability.applicationTelemetry`.
Validate rendered dashboards and rules with `tests/verify_application_telemetry.py` (Docker required).
