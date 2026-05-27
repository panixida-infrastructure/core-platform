# Platform Domains

Platform UI endpoints are served by Kubernetes through Envoy Gateway. DNS records point to `kubernetes_gateway_public_ipv4`.

```text
identity.panixida.ru   Keycloak
secrets.panixida.ru    OpenBao
grafana.panixida.ru    Grafana
metrics.panixida.ru    VictoriaMetrics
logs.panixida.ru       VictoriaLogs
traces.panixida.ru     VictoriaTraces
alerts.panixida.ru     Alertmanager
argocd.panixida.ru     Argo CD
headlamp.panixida.ru   Headlamp
```

Retired platform endpoints:

```text
traefik.panixida.ru
komodo.panixida.ru
auth.panixida.ru
sonar.panixida.ru
```

The DNS records are managed by OpenTofu in `opentofu/envs/production/dns.tf`. SonarQube migration is paused, so `sonar.panixida.ru` is intentionally not published.

TLS certificates for the Kubernetes UI endpoints are issued by cert-manager through the shared Envoy Gateway HTTP listener. The Timeweb LoadBalancer service is patched through EnvoyProxy to keep the public `443` port first and pass TCP traffic through to Envoy Gateway, where TLS is terminated.
