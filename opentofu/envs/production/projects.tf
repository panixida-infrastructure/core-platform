resource "twc_project" "infrastructure" {
  name        = "core-platform"
  description = ""

  lifecycle {
    prevent_destroy = true
  }
}
