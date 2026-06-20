data "twc_router_preset" "core_platform_minimal" {
  location   = var.location
  node_count = 1
  cpu        = 1
  ram        = 1
}

resource "twc_floating_ip" "core_platform_router_ipv4_msk" {
  availability_zone = "msk-1"
  ddos_guard        = false
  comment           = "core-platform Kubernetes router"

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_router" "core_platform_msk" {
  name       = "core-platform-router"
  preset_id  = data.twc_router_preset.core_platform_minimal.id
  project_id = twc_project.infrastructure.id

  networks {
    id              = twc_vpc.infrastructure_msk.id
    is_dhcp_enabled = true
  }

  ips {
    ip = twc_floating_ip.core_platform_router_ipv4_msk.ip

    nat {
      id = twc_vpc.infrastructure_msk.id
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}
