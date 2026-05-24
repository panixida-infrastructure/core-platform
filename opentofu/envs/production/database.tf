resource "twc_database_cluster" "postgres_database" {
  name              = "Postgres Database"
  type              = "postgres18"
  preset_id         = 533
  project_id        = 1619863
  availability_zone = "spb-3"
  is_external_ip    = true

  lifecycle {
    prevent_destroy = true
  }
}
