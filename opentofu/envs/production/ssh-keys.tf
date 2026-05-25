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
        [for server in self.used_by : server.name],
        "infrastructure"
      )
      error_message = "Infrastructure SSH key must be attached to infrastructure in Timeweb."
    }
  }
}
