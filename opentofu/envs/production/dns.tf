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

resource "twc_dns_rr" "portainer" {
  zone_id = data.twc_dns_zone.panixida_ru.id
  name    = "portainer"
  type    = "A"
  value   = var.platform_public_ipv4
}
