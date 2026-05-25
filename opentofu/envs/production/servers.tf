resource "twc_server" "infrastructure" {
  name              = "infrastructure"
  availability_zone = "msk-1"
  os_id             = 145
  preset_id         = 4803
  project_id        = twc_project.infrastructure.id
  ssh_keys_ids      = [twc_ssh_key.infrastructure_605568.id]

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [ssh_keys_ids]
  }
}
