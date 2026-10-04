variable "gitlab_token" {
  description = "Personal access token with the api scope. Pass it as TF_VAR_gitlab_token."
  type        = string
  sensitive   = true
}

variable "gitlab_base_url" {
  description = "GitLab API root, including /api/v4."
  type        = string
  default     = "http://localhost:8929/api/v4"
}

variable "tenants" {
  description = "Downstream tenant projects. tenant_orchestrator triggers these from the CI file it generates."
  type        = list(string)
  default     = ["atlas", "borealis", "cedar"]
}
