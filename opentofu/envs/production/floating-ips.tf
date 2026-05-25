resource "twc_floating_ip" "infrastructure_ipv4" {
  availability_zone = "msk-1"
  ddos_guard        = false
  comment           = ""

  resource {
    type = "server"
    id   = "8034806"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_floating_ip" "postgres_database_ipv4_legacy" {
  availability_zone = "spb-3"
  ddos_guard        = false
  comment           = ""

  resource {
    type = "dbaas"
    id   = "4104619"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_floating_ip" "postgres_database_ipv4_msk" {
  availability_zone = "msk-1"
  ddos_guard        = false
  comment           = ""

  resource {
    type = "dbaas"
    id   = twc_database_cluster.postgres_database_msk.id
  }

  lifecycle {
    prevent_destroy = true
  }
}

moved {
  from = twc_floating_ip.postgres_database_ipv4
  to   = twc_floating_ip.postgres_database_ipv4_legacy
}
