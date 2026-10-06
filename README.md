# Dev portal

Operators sign in with Keycloak and start a tenant release. The portal records who triggered it and, for production, the change request. GitLab project `tenant_orchestrator` fans that request out to the tenant projects.

## Read these

| Doc | What it is |
|---|---|
| [docs/rewrite.md](docs/rewrite.md) | Build the portal yourself, file by file, from an empty folder |
| [docs/tutorial.md](docs/tutorial.md) | How a click becomes tenant jobs, and where each rule lives |
| [docs/architecture.md](docs/architecture.md) | End-to-end picture: local wiring, sequence, variables, ledger |
| [docs/eks.md](docs/eks.md) | Helm install on EKS through the existing Istio gateway |

## Run locally

GitLab and the runner come from the second compose file. Keycloak and the portal come from the first.

```powershell
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml up -d --build portal
```

Open http://localhost:8000. Sign in as `devportal-user` / `devportal-pass`. The account `outsider-user` / `outsider-pass` is refused because it is not in the `dev-portal-users` group.

The GitLab API token used locally is `mock-secrets/gitlab-token.local`, mounted by the gitignored override. The committed `mock-secrets/gitlab-token` is a fake.

Ledger tests:

```powershell
python -m unittest tests.test_releases_store -v
```

Login and header checks against a running stack:

```powershell
powershell -File ./tests/test-security-hardening.ps1
```
