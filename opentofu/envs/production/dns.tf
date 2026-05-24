data "twc_dns_zone" "panixida_ru" {
  name = "panixida.ru"
}

resource "twc_dns_rr" "traefik" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "traefik"
  type    = "A"
  value   = var.platform_public_ipv4
}

resource "twc_dns_rr" "identity" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "identity"
  type    = "A"
  value   = var.platform_public_ipv4
}

resource "twc_dns_rr" "secrets" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "secrets"
  type    = "A"
  value   = var.platform_public_ipv4
}

resource "twc_dns_rr" "komodo" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "komodo"
  type    = "A"
  value   = var.platform_public_ipv4
}

resource "twc_dns_rr" "auth" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "auth"
  type    = "A"
  value   = var.platform_public_ipv4
}

resource "twc_dns_rr" "grafana" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "grafana"
  type    = "A"
  value   = var.platform_public_ipv4
}

resource "twc_dns_rr" "metrics" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "metrics"
  type    = "A"
  value   = var.platform_public_ipv4
}

resource "twc_dns_rr" "logs" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "logs"
  type    = "A"
  value   = var.platform_public_ipv4
}

resource "twc_dns_rr" "traces" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "traces"
  type    = "A"
  value   = var.platform_public_ipv4
}

resource "twc_dns_rr" "alerts" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "alerts"
  type    = "A"
  value   = var.platform_public_ipv4
}

resource "twc_dns_rr" "sonar" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "sonar"
  type    = "A"
  value   = var.platform_public_ipv4
}
