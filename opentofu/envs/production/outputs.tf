output "environment" {
  value = {
    name_prefix = local.name_prefix
    location    = var.location
    tags        = local.tags
  }
}

output "timeweb_inventory" {
  value = {
    projects = {
      infrastructure = twc_project.infrastructure.id
    }

    servers = {
      infrastructure = {
        id        = twc_server.infrastructure.id
        main_ipv4 = twc_server.infrastructure.main_ipv4
      }
    }

    networks = {
      core_platform_msk = twc_vpc.infrastructure_msk.id
    }

    kubernetes = {
      id                    = twc_k8s_cluster.core_platform.id
      version               = twc_k8s_cluster.core_platform.version
      status                = twc_k8s_cluster.core_platform.status
      network_driver        = var.k8s_network_driver
      default_node_group_id = twc_k8s_node_group.core_platform_default.id
    }

    network_drives = {
      core_platform_nvme = {
        id                 = twc_network_drive.core_platform_nvme.id
        availability_zone  = "msk-1"
        size_gb            = var.network_drive_size_gb
        storage_class_name = "nvme.network-drives.csi.timeweb.cloud"
      }
    }

    postgres_database = {
      id       = twc_database_cluster.postgres_database_msk.id
      networks = twc_database_cluster.postgres_database_msk.networks
      port     = twc_database_cluster.postgres_database_msk.port
    }

    tofu_state_bucket_id = data.twc_s3_bucket.tofu_state.id

    dns_records = {
      traefik  = "traefik.panixida.ru"
      identity = "identity.panixida.ru"
      secrets  = "secrets.panixida.ru"
      komodo   = "komodo.panixida.ru"
      auth     = "auth.panixida.ru"
      grafana  = "grafana.panixida.ru"
      metrics  = "metrics.panixida.ru"
      logs     = "logs.panixida.ru"
      traces   = "traces.panixida.ru"
      alerts   = "alerts.panixida.ru"
      sonar    = "sonar.panixida.ru"
    }
  }
}

output "core_platform_kubeconfig" {
  value     = twc_k8s_cluster.core_platform.kubeconfig
  sensitive = true
}
