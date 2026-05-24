variable "twc_token" {
  description = "Timeweb Cloud API token. Prefer TF_VAR_twc_token in environment, not tfvars."
  type        = string
  sensitive   = true
  default     = null
}

variable "name_prefix" {
  description = "Naming prefix for infrastructure resources."
  type        = string
  default     = "panixida"
}

variable "timeweb_project_name" {
  description = "Timeweb project name to use for managed resources."
  type        = string
  default     = null
}

variable "location" {
  description = "Timeweb location code, for example ru-1."
  type        = string
  default     = "ru-1"
}

variable "tags" {
  description = "Common metadata tags for modules that support tags."
  type        = map(string)
  default = {
    managed_by = "opentofu"
    owner      = "panixida-infrastructure"
  }
}
