resource "twc_project" "common" {
  name        = "Общий проект"
  description = ""

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_project" "infrastructure" {
  name        = "Инфраструктура"
  description = ""

  lifecycle {
    prevent_destroy = true
  }
}

resource "twc_project" "tactical_heroes" {
  name        = "tactical-heroes"
  description = ""

  lifecycle {
    prevent_destroy = true
  }
}
