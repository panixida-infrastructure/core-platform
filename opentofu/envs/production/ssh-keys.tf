resource "twc_ssh_key" "oldstrategyforge_245799" {
  name       = "OldStrategyForge"
  body       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPX6O0nnlxe9pdm7jHFuyqUHj8ygP5JvHLUCNYomOdra OldStrategyForge"
  is_default = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_ssh_key" "oldstrategyforge_273273" {
  name       = "OldStrategyForge"
  body       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICylT7W82gnw79fHJhilqzaKosHlIkGzTFgoYvBsMn4X OldStrategyForge"
  is_default = true

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_ssh_key" "infrastructure_605568" {
  name       = "infrastructure"
  body       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF24IfpOKOfreBxvhsTICQzi/r+qvc0kQX9RIAKKwLfL infrastructure"
  is_default = false

  lifecycle {
    prevent_destroy = true
  }
}
