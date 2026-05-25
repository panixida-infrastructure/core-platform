resource "twc_vpc" "infrastructure_msk" {
  name        = "core-platform-network"
  location    = "ru-3"
  subnet_v4   = "192.168.10.0/24"
  description = "Private network for core platform infrastructure in MSK-1."

  lifecycle {
    prevent_destroy = true
  }
}
