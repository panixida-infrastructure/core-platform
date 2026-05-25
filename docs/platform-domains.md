# Platform Domains

During the migration, existing public platform UI endpoints still point to the `infrastructure` server. The Kubernetes target is Envoy Gateway with DNS repointed to the Kubernetes LoadBalancer IP after the workloads are migrated.

```text
identity.panixida.ru   Keycloak
secrets.panixida.ru    OpenBao
grafana.panixida.ru    Grafana
metrics.panixida.ru    VictoriaMetrics
logs.panixida.ru       VictoriaLogs
traces.panixida.ru     VictoriaTraces
alerts.panixida.ru     Alertmanager
sonar.panixida.ru      SonarQube
```

New Kubernetes UI endpoints:

```text
argocd.panixida.ru     Argo CD
headlamp.panixida.ru   Headlamp
```

Retired after Kubernetes cutover:

```text
traefik.panixida.ru
komodo.panixida.ru
auth.panixida.ru
```

The DNS records are managed by OpenTofu in `opentofu/envs/production/dns.tf`. Before final cutover, update `platform_public_ipv4` to the Envoy Gateway LoadBalancer IPv4 and remove retired records in the same plan.

TLS certificates should be issued through cert-manager or Timeweb LoadBalancer SSL settings for the Envoy Gateway entrypoint.
