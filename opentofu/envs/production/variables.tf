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
  default     = "ru-3"
}

variable "kubernetes_gateway_public_ipv4" {
  description = "Public IPv4 address of the Envoy Gateway LoadBalancer for Kubernetes UI DNS records."
  type        = string
  default     = "186.246.9.205"
}

variable "k8s_version" {
  description = "Managed Kubernetes version for the core platform cluster."
  type        = string
  default     = "v1.35.4+k0s.0"
}

variable "k8s_network_driver" {
  description = "Managed Kubernetes CNI network driver."
  type        = string
  default     = "cilium"
}

variable "k8s_master_preset_id" {
  description = "Timeweb Managed Kubernetes master preset id. 2947 is Promo MSK."
  type        = number
  default     = 2947
}

variable "k8s_worker_preset_id" {
  description = "Timeweb Managed Kubernetes worker preset id. 2951 is Promo MSK 2 CPU / 2 GB / 40 GB."
  type        = number
  default     = 2951
}

variable "k8s_worker_node_count" {
  description = "Initial worker node count. Keep in sync with autoscaling min size on first create."
  type        = number
  default     = 4
}

variable "k8s_worker_min_size" {
  description = "Minimum autoscaling size for the default worker node group."
  type        = number
  default     = 4
}

variable "k8s_worker_max_size" {
  description = "Maximum autoscaling size for the default worker node group."
  type        = number
  default     = 6
}

variable "k8s_quality_worker_preset_id" {
  description = "Timeweb Managed Kubernetes worker preset id for quality tools. 1683 is MSK 2 CPU / 4 GB / 60 GB."
  type        = number
  default     = 1683
}

variable "k8s_quality_worker_node_count" {
  description = "Initial worker node count for the quality node group."
  type        = number
  default     = 1
}

variable "k8s_quality_worker_min_size" {
  description = "Minimum autoscaling size for the quality node group."
  type        = number
  default     = 1
}

variable "k8s_quality_worker_max_size" {
  description = "Maximum autoscaling size for the quality node group."
  type        = number
  default     = 2
}

variable "tags" {
  description = "Common metadata tags for modules that support tags."
  type        = map(string)
  default = {
    managed_by = "opentofu"
    owner      = "panixida-infrastructure"
  }
}
