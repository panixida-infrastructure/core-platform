# Managed Valkey monitoring

The shared Timeweb Valkey 9.1 instance serves caching, synchronization and gateway
rate limiting. Its metrics describe the whole instance, not individual dev/prod
applications. Dashboard: [Managed Valkey](https://grafana.panixida.ru/d/valkey-managed/valkey-managed).

## Source and credentials

Timeweb provides [database and host Prometheus exporters](https://timeweb.cloud/docs/dbaas/prometheus-exporter).
Both endpoints were verified against cluster `4225991`, including `redis_up=1`,
`valkey_version=9.1.0`, `maxmemory_policy=noeviction`, CPU and filesystem metrics.
The exporter also reports a Redis compatibility version; use `valkey_version`
to identify the running server.

Valkey remains on `192.168.10.6:6379` with no public IP. The Collector retrieves
metrics through Timeweb's **authenticated public HTTPS exporter**, not directly
through the private Valkey port. No extra server, database user or public database
IP is provisioned. The exporter has independent HTTP Basic credentials (`admin`
is the HTTP login, not a restored Valkey `default` account). Those credentials
access metrics; the Collector does not receive database command permissions.
The provider operates the exporter and its internal database identity/version.

`scripts/timeweb/sync-managed-valkey.py`, run by OpenTofu Apply, retrieves the
single replica's exporter credentials from `/api/v1/dbs/4225991/exporters` and
stores `OBSERVABILITY_TIMEWEB_VALKEY_EXPORTER_ID`, `_USERNAME` and `_PASSWORD` in
`secret/core-platform/observability`. Updates use CAS and preserve other secrets.
The existing namespace-scoped ExternalSecret synchronizes them into
`observability/observability-secrets`; the Collector reads that Secret as environment.
The existing OpenBao policies already permit this publication and read-only sync.

Publish the credentials and refresh the ExternalSecret **before** deploying the
Collector configuration. Rotation requires an ExternalSecret refresh followed by
a Collector restart; changing a Secret alone does not update container environment.
Do not print the secret or put credentials in URLs, Git, workflow artifacts or logs.

## Collection and dashboard

The two Collector jobs `timeweb-dbaas-valkey` and `timeweb-dbaas-valkey-node`
scrape every 30 seconds with a 10-second timeout and export to VictoriaMetrics.
Scrape rules use an explicit metric allowlist. No key names, values, channel
names or slowlog command arguments are retained. Logical database labels contain
numbers only and do not identify environments.

The Git-provisioned dashboard covers database/exporter availability, sample age,
uptime/resets, memory and maxmemory, OOM/write errors, clients/rejected connections,
blocked clients, operations/mean execution latency, hits/misses, key counts,
expiry/eviction, network, Pub/Sub counts, CPU, host memory, disk and AOF/RDB.

Limitations:

- HTTP `up=1` alone does not prove Valkey is reachable: check `redis_up` and the
  exporter's scrape-error metric as well. Missing samples stay unknown, not zero.
- Metrics are aggregate. Per-user/environment errors, application round-trip
  latency, channel delivery and lock correctness require application telemetry.
- Mean command latency excludes network/queue time and is not p95/p99.
- An idle hit ratio is undefined. Error counters can be absent before the first
  error; the dashboard substitutes zero only with a fresh successful database probe.
- Provider exporters may omit empty logical databases. Uptime-based restart counts
  can miss restarts during collection outages.
- AOF/RDB are local persistence, not backups or HA. RDB age alone is not an alert:
  `save 900 1` does not require a new snapshot when there have been no changes.

## Alerts and first response

All rules use `alert_owner=core-platform`, `environment=shared`, a dashboard panel
link and a short diagnostic description. vmalert sends them through the existing
Alertmanager → Telegram gateway route to the core-platform topic. No new Telegram
topic or direct bot integration is needed.

| Condition | Severity / hold | First response |
| --- | --- | --- |
| Valkey probe fails | Critical / 2m | Compare exporter availability; inspect Timeweb status and private connectivity. |
| Database metrics missing, HTTP failing or partial scrape | Warning / 2m; missing-data window 3m | Check Collector logs, exporter authentication and ExternalSecret refresh. |
| Host metrics missing/failing | Warning / 5m; missing-data window 3m | Check the independent node-exporter endpoint. |
| Accounted memory >80% / >90% of actual maxmemory | Warning / 10m; critical / 5m | Check TTLs and key growth; plan/expand capacity. Preserve coordination/session keys. |
| New OOM errors in 5m | Critical / 1m | Check memory and rejected operations; noeviction preserves existing keys but rejects growing writes. |
| Failed/rejected write commands in 5m | Warning / 2m | Inspect command names, OOM, Lua errors and ACLs in application logs. |
| Rejected connections in 5m | Warning / 2m | Compare client count with maxclients; investigate reconnect storms and connection reuse. |
| Mean command execution >50ms, at least 100 calls/5m | Warning / 10m | Inspect command mix, CPU and persistence. Sparse traffic is excluded. |
| AOF write/rewrite failure while AOF enabled | Critical / 2m | Check disk and provider storage before considering a restart. |
| RDB background-save failure | Warning / 5m | Check filesystem space and host memory. |
| Filesystem free space <20% / <10% | Warning / 15m; critical / 5m | Inspect device/mountpoint and AOF rewrite headroom; expand storage through IaC. |
| New key evictions in 5m | Critical / 1m | Verify noeviction and assess application coordination state. |

Memory accounting subtracts `mem_not_counted_for_eviction` and uses the exported
`maxmemory`, not tariff RAM. The initial observed maxmemory was `1,376,000,000`
bytes (about 1.28 GiB) on the 2 GiB tariff. Database capacity/error rules require a
successful probe sample younger than 180 seconds. CPU/blocked-client graphs have
no fixed alerts until workload behavior warrants a threshold.

## Verification

```sh
python3 -B -m unittest discover -s tests -p test_managed_valkey.py
helm lint kubernetes/charts/core-platform-workloads
helm template platform-workloads kubernetes/charts/core-platform-workloads > /tmp/core-platform-workloads.yaml
python3 -B tests/verify_valkey_alerts.py /tmp/core-platform-workloads.yaml
python3 -B tests/verify_metric_links.py /tmp/core-platform-workloads.yaml
```

The last two commands require Docker and use the deployed vmalert-tool version.
Fixtures cover healthy/idle workloads, OOM/write/connection errors, counter resets,
memory thresholds and excluded buffers, low-volume latency, AOF/RDB, disks,
evictions, scrape failure, missing/stale data and recovery. Every dashboard query
and alert panel link is checked. CI runs these checks with the other platform tests.

After GitOps sync, confirm both jobs in VictoriaMetrics, fresh `redis_up=1`,
`redis_exporter_last_scrape_error=0`, expected maxmemory, host metrics and Grafana
provisioning. Verify notifications with a temporary synthetic vmalert rule carrying
`alert_owner=tests`, a unique test identifier and automatic recovery, then confirm
both firing and resolved deliveries in the gateway logs. Do not induce OOM, stop
Valkey or execute destructive database commands to test alerts. Remove all temporary
test resources afterward.
