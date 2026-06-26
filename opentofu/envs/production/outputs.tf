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

    routers = {
      core_platform_msk         = twc_router.core_platform_msk.id
      core_platform_msk_ipv4    = twc_floating_ip.core_platform_router_ipv4_msk.ip
      core_platform_msk_v2      = twc_router.core_platform_msk_v2.id
      core_platform_msk_v2_ipv4 = twc_floating_ip.core_platform_router_v2_ipv4_msk.ip
    }

    kubernetes = {
      id                              = twc_k8s_cluster.core_platform.id
      version                         = twc_k8s_cluster.core_platform.version
      status                          = twc_k8s_cluster.core_platform.status
      network_driver                  = var.k8s_network_driver
      worker_node_group_id            = twc_k8s_node_group.core_platform_default.id
      default_node_group_id           = twc_k8s_node_group.core_platform_default.id
      infrastructure_v2_node_group_id = twc_k8s_node_group.core_platform_infrastructure_v2.id
    }

    postgres_database = {
      id       = twc_database_cluster.postgres_database_msk.id
      networks = twc_database_cluster.postgres_database_msk.networks
      port     = twc_database_cluster.postgres_database_msk.port
    }

    tofu_state_bucket_id = data.twc_s3_bucket.tofu_state.id

    dns_records = {
      identity                = "identity.panixida.ru"
      secrets                 = "secrets.panixida.ru"
      grafana                 = "grafana.panixida.ru"
      argocd                  = "argocd.panixida.ru"
      k8s                     = "k8s.panixida.ru"
      kargo                   = "kargo.panixida.ru"
      sonar                   = "sonar.panixida.ru"
      dotnet_template_api     = "api.dotnet-template.panixida.ru"
      dotnet_template_api_dev = "dev.api.dotnet-template.panixida.ru"
    }
  }
}

output "core_platform_kubeconfig" {
  value     = twc_k8s_cluster.core_platform.kubeconfig
  sensitive = true
}
