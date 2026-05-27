# Platform Domains

Platform UI endpoints are served by Kubernetes through Envoy Gateway. DNS records point to `kubernetes_gateway_public_ipv4`.

```text
identity.panixida.ru   Keycloak
secrets.panixida.ru    OpenBao
grafana.panixida.ru    Grafana
argocd.panixida.ru     Argo CD
headlamp.panixida.ru   Headlamp
```

Retired platform endpoints:

```text
traefik.panixida.ru
komodo.panixida.ru
auth.panixida.ru
sonar.panixida.ru
metrics.panixida.ru
logs.panixida.ru
traces.panixida.ru
alerts.panixida.ru
```

The DNS records are managed by OpenTofu in `opentofu/envs/production/dns.tf`. SonarQube migration is paused, so `sonar.panixida.ru` is intentionally not published.

TLS certificates for the Kubernetes UI endpoints are issued by cert-manager through the shared Envoy Gateway HTTP listener. Kubernetes-side Envoy and cert-manager are configured for HTTPS; the remaining Timeweb LoadBalancer TLS behavior is tracked with Timeweb support and can be fixed without moving DNS back to the retired server.

VictoriaMetrics, VictoriaLogs, VictoriaTraces, and Alertmanager are internal ClusterIP services. Their public routes and DNS records are intentionally absent; operators should use Grafana for dashboards, logs, traces, and alert visibility.
