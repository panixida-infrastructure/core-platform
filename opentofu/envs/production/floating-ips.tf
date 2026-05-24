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

resource "twc_floating_ip" "tacticalheroes_dev_ipv4" {
  availability_zone = "spb-3"
  ddos_guard        = false
  comment           = ""
  ptr               = "1378593-ck97193.tw1.ru"

  resource {
    type = "server"
    id   = "3761019"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_floating_ip" "postgres_database_ipv4" {
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
