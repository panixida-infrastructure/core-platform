resource "twc_project" "infrastructure" {
  # Timeweb does not allow deleting the default project, so core-platform is
  # managed on the account default project id.
  name        = "core-platform"
  description = ""

  lifecycle {
    prevent_destroy = true
  }
}
