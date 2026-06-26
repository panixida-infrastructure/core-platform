resource "twc_k8s_cluster" "core_platform" {
  name              = "core-platform"
  description       = "Managed Kubernetes cluster for the core platform."
  version           = var.k8s_version
  network_driver    = var.k8s_network_driver
  high_availability = false
  ingress           = false
  preset_id         = var.k8s_master_preset_id
  network_id        = twc_vpc.infrastructure_msk.id
  project_id        = twc_project.infrastructure.id

  oidc_provider {
    name           = "keycloak-panixida"
    issuer_url     = "https://identity.panixida.ru/realms/panixida"
    client_id      = "kubernetes"
    username_claim = "preferred_username"
    groups_claim   = "groups"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Keep the Terraform address for state compatibility; the live worker group
# was migrated away from the retired core-platform-default group.
resource "twc_k8s_node_group" "core_platform_default" {
  cluster_id        = twc_k8s_cluster.core_platform.id
  name              = "core-platform-infrastructure"
  preset_id         = var.k8s_worker_preset_id
  node_count        = var.k8s_worker_node_count
  is_autoscaling    = false
  is_autohealing    = true
  public_ip_enabled = false
  virtual_router_id = twc_router.core_platform_msk.id

  labels {
    key   = "panixida.ru/node-pool"
    value = "core-platform"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_k8s_node_group" "core_platform_infrastructure_v2" {
  cluster_id        = twc_k8s_cluster.core_platform.id
  name              = "core-platform-infrastructure-v2"
  preset_id         = var.k8s_infrastructure_v2_worker_preset_id
  node_count        = var.k8s_infrastructure_v2_worker_node_count
  is_autoscaling    = false
  is_autohealing    = true
  public_ip_enabled = false
  virtual_router_id = twc_router.core_platform_msk.id

  labels {
    key   = "panixida.ru/node-pool"
    value = "core-platform"
  }

  lifecycle {
    prevent_destroy = true
  }
}
