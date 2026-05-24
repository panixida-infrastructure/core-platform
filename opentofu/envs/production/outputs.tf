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
      common          = twc_project.common.id
      infrastructure  = twc_project.infrastructure.id
      tactical_heroes = twc_project.tactical_heroes.id
    }

    servers = {
      tacticalheroes_dev = {
        id        = twc_server.tacticalheroes_dev.id
        main_ipv4 = twc_server.tacticalheroes_dev.main_ipv4
      }
      infrastructure = {
        id        = twc_server.infrastructure.id
        main_ipv4 = twc_server.infrastructure.main_ipv4
      }
    }

    postgres_database = {
      id       = twc_database_cluster.postgres_database.id
      networks = twc_database_cluster.postgres_database.networks
      port     = twc_database_cluster.postgres_database.port
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
