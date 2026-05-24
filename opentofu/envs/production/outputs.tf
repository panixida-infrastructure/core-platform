output "environment" {
  value = {
    name_prefix = local.name_prefix
    location    = var.location
    tags        = local.tags
  }
}
