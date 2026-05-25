resource "twc_database_cluster" "postgres_database_legacy" {
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

moved {
  from = twc_database_cluster.postgres_database
  to   = twc_database_cluster.postgres_database_legacy
}

resource "twc_database_cluster" "postgres_database_msk" {
  name              = "Postgres Database"
  type              = "postgres18"
  preset_id         = 1173
  project_id        = twc_project.infrastructure.id
  availability_zone = "msk-1"
  is_external_ip    = true

  network {
    id = twc_vpc.infrastructure_msk.id
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_database_backup_schedule" "postgres_database" {
  cluster_id        = twc_database_cluster.postgres_database_msk.id
  enabled           = true
  interval          = "day"
  copy_count        = 7
  creation_start_at = "2026-05-26T00:00:00Z"
}
