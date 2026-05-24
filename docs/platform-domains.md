# Platform Domains

All public platform UI endpoints are routed through Traefik on the infrastructure server.

```text
traefik.panixida.ru    Traefik dashboard
identity.panixida.ru   Keycloak
secrets.panixida.ru    OpenBao
portainer.panixida.ru  Portainer
```

The DNS records are managed by OpenTofu in `opentofu/envs/production/dns.tf` and point to the infrastructure server public IPv4.

TLS certificates are issued by Traefik through Let's Encrypt HTTP-01 challenges.
