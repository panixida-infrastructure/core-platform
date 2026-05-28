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

    postgres_database = {
      id       = twc_database_cluster.postgres_database_msk.id
      networks = twc_database_cluster.postgres_database_msk.networks
      port     = twc_database_cluster.postgres_database_msk.port
    }

    tofu_state_bucket_id = data.twc_s3_bucket.tofu_state.id

    dns_records = {
      identity = "identity.panixida.ru"
      secrets  = "secrets.panixida.ru"
      grafana  = "grafana.panixida.ru"
      argocd   = "argocd.panixida.ru"
      headlamp = "headlamp.panixida.ru"
      sonar    = "sonar.panixida.ru"
    }
  }
}

output "core_platform_kubeconfig" {
  value     = twc_k8s_cluster.core_platform.kubeconfig
  sensitive = true
}
