data "twc_dns_zone" "panixida_ru" {
  name = "panixida.ru"
}

resource "twc_dns_rr" "identity" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "identity"
  type    = "A"
  value   = var.kubernetes_gateway_public_ipv4
}

resource "twc_dns_rr" "secrets" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "secrets"
  type    = "A"
  value   = var.kubernetes_gateway_public_ipv4
}

resource "twc_dns_rr" "grafana" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "grafana"
  type    = "A"
  value   = var.kubernetes_gateway_public_ipv4
}

resource "twc_dns_rr" "argocd" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "argocd"
  type    = "A"
  value   = var.kubernetes_gateway_public_ipv4
}

resource "twc_dns_rr" "headlamp" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "headlamp"
  type    = "A"
  value   = var.kubernetes_gateway_public_ipv4
}

resource "twc_dns_rr" "sonar" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "sonar"
  type    = "A"
  value   = var.kubernetes_gateway_public_ipv4
}
