resource "twc_server" "tacticalheroes_dev" {
  name              = "TacticalHeroes.Dev"
  availability_zone = "spb-3"
  os_id             = 47
  preset_id         = 2455
  project_id        = 1152653
  ssh_keys_ids      = [twc_ssh_key.oldstrategyforge_273273.id]

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_server" "infrastructure" {
  name              = "infrastructure"
  availability_zone = "msk-1"
  os_id             = 145
  preset_id         = 4803
  project_id        = 1619863
  ssh_keys_ids      = [twc_ssh_key.infrastructure_605568.id]

  lifecycle {
    prevent_destroy = true
  }
}
