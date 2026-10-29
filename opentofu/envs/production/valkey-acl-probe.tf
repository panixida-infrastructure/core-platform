resource "random_password" "valkey_acl_probe" {
  length  = 30
  special = false
}

resource "twc_database_user" "valkey_acl_probe" {
  cluster_id = twc_database_cluster.valkey_database_msk.id
  login      = "core_platform_acl_probe"
  password   = random_password.valkey_acl_probe.result
  for_all    = true
  privileges = ["CONNECTION", "DANGEROUS"]
}

output "valkey_acl_probe_password" {
  value     = random_password.valkey_acl_probe.result
  sensitive = true
}
