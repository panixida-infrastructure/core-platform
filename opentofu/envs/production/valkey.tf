resource "random_password" "valkey_admin" {
  length  = 30
  special = false
}

locals {
  valkey_users = {
    development = "tactical_heroes_user_dev"
    production  = "tactical_heroes_user_prod"
  }
  valkey_application_privileges = ["CONNECTION", "READ", "WRITE", "PUBSUB", "SCRIPTING", "TRANSACTION"]
  valkey_gateway_privileges     = ["CONNECTION", "READ", "WRITE", "SCRIPTING"]
}

resource "random_password" "valkey_application" {
  for_each = local.valkey_users
  length   = 30
  special  = false
}

resource "random_password" "valkey_gateway" {
  length  = 30
  special = false
}

resource "twc_database_cluster" "valkey_database_msk" {
  name                    = "valkey"
  type                    = "valkey9_1"
  preset_id               = 5067 # 1 CPU / 2 GiB RAM / 10 GiB disk, 1700 RUB/month.
  project_id              = twc_project.infrastructure.id
  availability_zone       = "msk-1"
  is_external_ip          = false
  is_enabled_public_ipv6  = false
  is_soft_remove_recovery = false

  network {
    id = twc_vpc.infrastructure_msk.id
  }

  admin {
    # Create-only bootstrap; sync-managed-valkey.py retires it after user checks.
    login    = "default"
    password = random_password.valkey_admin.result
  }

  config_parameters = {
    maxmemory-policy = "noeviction"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_database_user" "valkey_application" {
  for_each   = local.valkey_users
  cluster_id = twc_database_cluster.valkey_database_msk.id
  login      = each.value
  password   = random_password.valkey_application[each.key].result
  for_all    = true
  privileges = local.valkey_application_privileges
}

resource "twc_database_user" "valkey_gateway" {
  cluster_id = twc_database_cluster.valkey_database_msk.id
  login      = "envoy_gateway_user"
  password   = random_password.valkey_gateway.result
  for_all    = true
  privileges = local.valkey_gateway_privileges
}

resource "twc_database_backup_schedule" "valkey_database" {
  cluster_id        = twc_database_cluster.valkey_database_msk.id
  enabled           = false
  interval          = "day"
  copy_count        = 1 # Required by the provider even when the schedule is disabled.
  creation_start_at = "2026-10-29T00:00:00"

  lifecycle {
    ignore_changes = [creation_start_at]
  }
}

output "managed_valkey" {
  value = {
    id         = twc_database_cluster.valkey_database_msk.id
    network_id = twc_vpc.infrastructure_msk.id
    host = one(flatten([
      for network in twc_database_cluster.valkey_database_msk.networks :
      [for address in network.ips : address.ip] if network.type == "local"
    ]))
    port                = twc_database_cluster.valkey_database_msk.port
    retire_default_user = true
    privileges          = local.valkey_application_privileges
    users = {
      for environment, user in twc_database_user.valkey_application :
      environment => user.login
    }
    gateway = {
      username   = twc_database_user.valkey_gateway.login
      privileges = local.valkey_gateway_privileges
    }
  }
}

output "managed_valkey_password" {
  value     = random_password.valkey_admin.result
  sensitive = true
}

output "managed_valkey_user_passwords" {
  value = {
    for environment, password in random_password.valkey_application :
    environment => password.result
  }
  sensitive = true
}

output "managed_valkey_gateway_password" {
  value     = random_password.valkey_gateway.result
  sensitive = true
}
