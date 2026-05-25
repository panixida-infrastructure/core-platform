resource "twc_network_drive" "core_platform_nvme" {
  name              = "core-platform-nvme"
  availability_zone = "msk-1"
  preset_id         = var.network_drive_preset_id
  size              = var.network_drive_size_gb
  comment           = ""

  lifecycle {
    prevent_destroy = true
  }
}
