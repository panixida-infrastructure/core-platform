resource "twc_ssh_key" "oldstrategyforge_273273" {
  name       = "OldStrategyForge"
  body       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICylT7W82gnw79fHJhilqzaKosHlIkGzTFgoYvBsMn4X OldStrategyForge"
  is_default = true

  lifecycle {
    prevent_destroy = true

    postcondition {
      condition = contains(
        [for server in self.used_by : tostring(server.id)],
        tostring(twc_server.tacticalheroes_dev.id)
      )
      error_message = "OldStrategyForge SSH key must be attached to TacticalHeroes.Dev in Timeweb."
    }
  }
}

removed {
  from = twc_ssh_key.oldstrategyforge_245799

  lifecycle {
    destroy = false
  }
}

resource "twc_ssh_key" "infrastructure_605568" {
  name       = "infrastructure"
  body       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF24IfpOKOfreBxvhsTICQzi/r+qvc0kQX9RIAKKwLfL infrastructure"
  is_default = false

  lifecycle {
    prevent_destroy = true

    postcondition {
      condition = contains(
        [for server in self.used_by : tostring(server.id)],
        tostring(twc_server.infrastructure.id)
      )
      error_message = "Infrastructure SSH key must be attached to infrastructure in Timeweb."
    }
  }
}
