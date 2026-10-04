resource "gitlab_group" "tenants" {
  name        = "Tenants"
  path        = "tenants"
  description = "Downstream tenant projects triggered by tenant_orchestrator."
}

resource "gitlab_project" "orchestrator" {
  name                   = "tenant_orchestrator"
  path                   = "tenant_orchestrator"
  description            = "Portal entrypoint. Builds a CI file that triggers the tenant pipelines."
  visibility_level       = "private"
  default_branch         = "main"
  initialize_with_readme = true
}

resource "gitlab_project" "tenant" {
  for_each = toset(var.tenants)

  name                   = each.key
  path                   = each.key
  namespace_id           = gitlab_group.tenants.id
  description            = "Downstream pipeline for ${each.key}, triggered by tenant_orchestrator."
  visibility_level       = "private"
  default_branch         = "main"
  initialize_with_readme = true
}

resource "gitlab_repository_file" "orchestrator_pipeline" {
  project        = gitlab_project.orchestrator.id
  file_path      = ".gitlab-ci.yml"
  branch         = "main"
  content        = file("${path.module}/pipeline/orchestrator-ci.yml")
  encoding       = "text"
  author_email   = "terraform@localhost"
  author_name    = "Terraform"
  commit_message = "Add the tenant orchestrator pipeline"
}

resource "gitlab_repository_file" "orchestrator_generator" {
  project        = gitlab_project.orchestrator.id
  file_path      = "scripts/generate-downstream.sh"
  branch         = "main"
  content        = file("${path.module}/pipeline/generate-downstream.sh")
  encoding       = "text"
  author_email   = "terraform@localhost"
  author_name    = "Terraform"
  commit_message = "Add the downstream CI generator"
}

# Each tenant accepts a CI job token from tenant_orchestrator, which is what
# the generated pipeline uses to start the downstream project.
resource "gitlab_project_job_token_scope" "orchestrator_may_trigger" {
  for_each = gitlab_project.tenant

  project           = each.value.id
  target_project_id = gitlab_project.orchestrator.id
}

resource "gitlab_repository_file" "pipeline" {
  for_each = gitlab_project.tenant

  project        = each.value.id
  file_path      = ".gitlab-ci.yml"
  branch         = "main"
  content        = file("${path.module}/pipeline/gitlab-ci.yml")
  encoding       = "text"
  author_email   = "terraform@localhost"
  author_name    = "Terraform"
  commit_message = "Add the shared tenant pipeline"

  depends_on = [gitlab_project.tenant]
}
