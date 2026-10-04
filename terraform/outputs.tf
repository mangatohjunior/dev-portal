output "tenant_orchestrator" {
  description = "Project the portal triggers. Set GITLAB_PROJECT_ID to this id."
  value = {
    id  = gitlab_project.orchestrator.id
    url = gitlab_project.orchestrator.web_url
  }
}

output "tenant_projects" {
  description = "Downstream projects started by the CI file tenant_orchestrator generates."
  value = {
    for name, project in gitlab_project.tenant : name => {
      id  = project.id
      url = project.web_url
    }
  }
}
