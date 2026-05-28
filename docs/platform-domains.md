# Platform Domains

Platform UI endpoints are served by Kubernetes through Envoy Gateway. DNS records point to `kubernetes_gateway_public_ipv4`.

```text
identity.panixida.ru   Keycloak
secrets.panixida.ru    OpenBao
grafana.panixida.ru    Grafana
argocd.panixida.ru     Argo CD
k8s.panixida.ru        Headlamp
sonar.panixida.ru      SonarQube, currently disabled on the free Kubernetes worker preset
```

Retired platform endpoints:

```text
traefik.panixida.ru
komodo.panixida.ru
auth.panixida.ru
metrics.panixida.ru
logs.panixida.ru
traces.panixida.ru
alerts.panixida.ru
headlamp.panixida.ru
```

The DNS records are managed by OpenTofu in `opentofu/envs/production/dns.tf`.

TLS certificates for the Kubernetes UI endpoints are issued by cert-manager through the shared Envoy Gateway HTTP listener. The Timeweb LoadBalancer is configured as TCP passthrough, so public HTTPS traffic reaches Envoy Gateway and uses the cert-manager certificates.

VictoriaMetrics, VictoriaLogs, VictoriaTraces, and Alertmanager are internal ClusterIP services. Their public routes and DNS records are intentionally absent; operators should use Grafana for dashboards, logs, traces, and alert visibility.
