# Rewrite the portal from start to finish

Use this when you want to type the project again yourself and understand why each file exists. The other docs describe the finished system. This one is the build order.

[tutorial.md](tutorial.md) is what a click does. [architecture.md](architecture.md) is the picture of the running pieces. [eks.md](eks.md) is the cluster install. Come back to those when a step mentions GitLab, Keycloak, or EKS and you want the diagram.

Work in a fresh folder. After each step, the check at the bottom of that step should pass before you start the next one. If a check fails, stay on that step. Later files assume the earlier ones already behave.

## What you are building

An operator signs in with Keycloak. They pick an environment, one or more tenants, and a pipeline. The portal records who asked, and for production which change request they used. It then asks one GitLab project, `tenant_orchestrator`, to start the work. That project starts a pipeline in each named tenant project.

Three records stay separate:

| Question | Where the answer lives |
|---|---|
| Who may open the app? | Keycloak group `dev-portal-users` |
| Who pressed the button, and which change request covered production? | SQLite ledger |
| Did the jobs succeed? | GitLab |

You finish when a signed-in operator can trigger `atlas` on dev, see the popup wait for GitLab, and find a row on the Releases page. An account outside the group never gets a session. A second dev trigger is refused while the first pipeline is still running.

## The tree you end with

```
main.py                 wires the app
app/                    HTTP behavior
  startup.py            token and ledger on boot
  middleware.py         session, headers, sign-in gate
  trigger.py            start a release
  pipelines.py          poll a pipeline this portal started
  releases.py           chart, production table, CSV
config.py               every knob, from environment variables
model.py                the JSON body of a trigger
auth.py                 Keycloak sign-in and sign-out
gitlab_client.py        create a pipeline, read its status
releases_store.py       SQLite ledger and the environment lock
logging_utils.py        one-line audit events
public/                 the two pages
tests/test_releases_store.py
Dockerfile
docker-compose.yaml
docker-compose.gitlab.yaml
keycloak/realm-export.json
terraform/              GitLab projects and pipeline files
helm/dev-portal/        EKS, through the existing Istio gateway
```

`main.py` stays small on purpose. If a behavior is hard to find, it belongs in `app/` or in one of the root modules, not back in `main.py`.

## 1. Project shell

Create the folder, a git repo, and `.gitignore`. Ignore `.env`, `docker-compose.override.yaml`, `mock-secrets/gitlab-token.local`, `helm/**/values-local.yaml`, `data/`, `*.db`, `.terraform/`, `*.tfstate`, and `__pycache__/`.

`requirements.txt` pins the libraries the portal imports:

```
fastapi==0.110.0
uvicorn==0.27.1
pydantic==2.6.3
httpx==0.27.0
itsdangerous==2.2.0
```

`itsdangerous` is what signs the session cookie. You will not import it yourself. Starlette's session middleware does.

**Done when:** `pip install -r requirements.txt` succeeds and `git status` does not list a database or a real token.

## 2. `config.py`

This is the first real file because every other module reads it.

One class, `Config`. Every value comes from an environment variable, with a local default:

- GitLab base URL, project id (`root/tenant_orchestrator`), and the browser pipelines URL.
- Build the API URL here. Encode the project path so the slash in `root/tenant_orchestrator` becomes `%2F`.
- Token file path, and `GITLAB_TOKEN` as a local fallback.
- Keycloak internal URL (the portal calls this), external URL (the browser is sent here), realm, client id, client secret, redirect URI.
- `PORTAL_PUBLIC_URL`. Logout uses this. Do not build that address from the incoming Host header.
- Required group, default `dev-portal-users`. Empty string means skip the group check.
- Session secret, secure-cookie flag, and an eight-hour max age.
- `RELEASES_DB_PATH`.

Add `assert_runtime_config()`. Refuse to start when the session secret is shorter than 16 characters, when the Keycloak client secret is empty, or when HTTPS is on and the secret is still the local default `dev-session-secret-change-me`.

**Done when:** importing `Config` prints the local GitLab API URL with `root%2Ftenant_orchestrator` in it, and `assert_runtime_config()` raises while the client secret is empty.

## 3. `logging_utils.py`

A logger named `dev-portal` that writes one line to stdout. `audit_log(event, username, detail)` writes `AUDIT event=... username=... detail=...`.

Strip characters that are not printable, and strip newlines, before the line is written. A username with a newline would otherwise look like a second audit event.

**Done when:** a detail string that contains a newline comes out as one log line.

## 4. `model.py`

`DeploymentRequest` is the JSON body of `POST /api/trigger-deployment`. Extra fields are rejected.

Fields: `targetEnvironment` (`dev`, `test`, `preprod`, `prod`), `allowProd`, `tenantList`, `pipelineTrigger` (`tenant_baseline`, `kasm_vdi`, `tenant_services`), `taint`, and the optional replace fields plus `crNumber`.

Rules to enforce in one validator:

- Production requires `allowProd` and a change-request id shaped like `CHG0012345`. Store it uppercased. Any other environment stores `crNumber` as `None`.
- Split `tenantList` on commas, trim, drop blanks and duplicates. Each name is letters, numbers, `_`, or `-`. At most 20. Store them joined with commas and no spaces.
- With taint off, clear the replace fields so they cannot be smuggled through.
- With taint on, require the field that matches the pipeline: a Terraform address (`module.database`), a Kasm state (`KASM`), or at least one service from the fixed list `airflow`, `cloudbeaver`, `gitlab_runner`, `immuta`, `jupyterhub`, `mlflow`, `s3uploader`, `superset`.

**Done when:** `atlas;curl` is rejected, `chg0012345` on prod becomes `CHG0012345`, a dev body with a change request stores `None`, and an unknown service is rejected.

## 5. `releases_store.py`

This is the largest module. Write it before the HTTP routes, and write `tests/test_releases_store.py` beside it. The tests are how you know the rules, not the pages, are right.

SQLite file from `Config.RELEASES_DB_PATH`. Turn on WAL. Close every connection. On Windows an open handle locks the file.

Table `releases`: who, when (UTC `YYYY-MM-DDTHH:MM:SSZ`), environment, tenants, pipeline, taint, change request, change summary, GitLab pipeline id, web URL, and `pipeline_status`. Checks: environment is one of the four names; production rows have a change request; other rows have `NULL`.

`pipeline_status` is the column that changes later. Everything else in the row stays fixed.

Table `environment_lock`: one row per environment, a pipeline id, state `reserving` or `running`, a timestamp, and a random `reservation_token`.

Functions worth naming as you write them:

- `record_release` inserts one row. Refuse production without a change request. Drop a non-prod change request. Keep a GitLab `web_url` only when it starts with the configured GitLab base URL and contains no backslash, `@`, or whitespace.
- `portal_started_pipeline` is true only when that id is already in `releases`.
- `reserve_environment` inserts the lock and returns the token. A second insert for the same environment raises `EnvironmentBusy`.
- `release_environment` deletes only the row with that token, and only while it is still `reserving`.
- `note_pipeline_running` attaches the GitLab id to that same token. It returns false when the hold is already gone.
- `release_stale_reservation` deletes a `reserving` row older than a few minutes. It must not delete a newer hold.
- `set_pipeline_status` stores only a short lowercase status word.
- `list_releases` is every environment, oldest first. `list_prod_audit` and `prod_audit_csv` are production only. Prefix a CSV cell that starts with `=`, `+`, `-`, or `@` so a spreadsheet does not treat it as a formula.

**Done when:** `python -m unittest tests.test_releases_store -v` passes, including a second reserve on `dev` failing while `prod` still succeeds, and a stale token failing to release a newer hold.

## 6. `gitlab_client.py`

Two functions. Both send `PRIVATE-TOKEN`, time out at 20 seconds, and do not follow redirects. A redirect would send the token to whatever host answered.

`trigger_pipeline` POSTs to the URL from `config.py`, ref `main`, with these variables every time: `TARGET_ENV`, `TENANT_LIST`, `PIPELINE_TRIGGER`, `TAINT`, `TRIGGERED_BY`, `CR_NUMBER`, `REPLACE_RESOURCE`, `TF_REFRESH`, `REPLACE_STATE`, `SERVICES_LIST`. Unused ones are empty strings. GitLab status other than 200 or 201 becomes HTTP 502 with a short message. Do not return GitLab's error body to the operator, and do not log it.

`get_pipeline_status` GETs that project's pipeline and returns only `id` and `status`. A 404 becomes "Pipeline not found."

**Done when:** you can read the function and point at the ten variable names, and a connection error becomes 502 "Failed to connect to GitLab."

## 7. `auth.py`

An `APIRouter` for the pages that do not need a session. `PUBLIC_PATHS` is `/login`, `/login/keycloak`, `/auth/callback`, and `/logout`. The middleware in the next steps imports that set.

`/login` renders the landing page. `?reason=` picks one sentence: `unauthorized`, `logout`, `forbidden`, `failed`. The logout sentence is exactly `You have been logged out.`

`/login/keycloak` stores a random `state` in the session and redirects the browser to `KEYCLOAK_EXTERNAL_URL`.

`/auth/callback` checks `state`, then calls `KEYCLOAK_INTERNAL_URL` to exchange the code and read userinfo. Timeout 20 seconds, no redirects. Log the status code only. Keep a session only when `groups` contains `KEYCLOAK_REQUIRED_GROUP`. Store `preferred_username` and `email` in the cookie. Discard the access token. Clear the session before you store the user.

`/logout` clears the cookie and redirects to Keycloak's end-session URL. `post_logout_redirect_uri` is `PORTAL_PUBLIC_URL/login?reason=logout`.

`/api/me` returns the username and email for the header of the pages.

**Done when:** you can trace one successful login and one forbidden login on paper, and both end on `/login` except the success, which ends on `/`.

## 8. `app/startup.py`

`__init__.py` can be a one-line docstring. It marks `app` as a package so `main.py` can import these modules.

`lifespan` runs once when the process starts. Call `assert_runtime_config()`. Read the GitLab token from the file, or from `GITLAB_TOKEN`. If both are missing, print the error and exit. Call `init_db()`. Keep the token on a module variable, `gitlab_token`, so the trigger and pipeline modules can read it later.

**Done when:** starting the app with no token exits, and starting it with a token file prints that the ledger is ready.

## 9. `app/middleware.py`

`install_middleware(app)` registers three layers. Order matters.

1. `require_auth`. Changing methods (`POST`, `PUT`, `PATCH`, `DELETE`) with an `Origin` header must match `PORTAL_PUBLIC_URL` or the request's own origin. Paths in `PUBLIC_PATHS` pass through. Anyone else with no session gets 401 on `/api/...` and a redirect to `/login?reason=unauthorized` on pages.
2. `security_headers`, registered after the gate so it also covers those 401s and redirects. Set nosniff, deny frames, no-referrer, a locked-down permissions policy, same-origin opener and resource policy, and `Cache-Control: no-store`. The content security policy allows the Tailwind CDN and inline styles, because the pages are written that way. Add HSTS only when the session cookie is marked secure.
3. `SessionMiddleware` added last, so it runs first and fills `request.session` before the gate looks at it. `same_site` is `lax`. `https_only` follows the secure-cookie flag. Max age is eight hours.

Then include the auth router.

**Done when:** a request with no cookie to `/api/me` is 401, and the response includes the security headers. `/login` still returns the page.

## 10. `app/releases.py`

A router for the history, before the trigger exists. You can load sample rows and see them.

- `GET /api/releases` returns every environment.
- `GET /api/releases/audit` returns production only.
- `GET /api/releases/audit.csv` downloads that production list.
- `GET /releases` serves `public/releases.html`.

`fixtures/sample-releases.json` plus a small importer lets you fill the chart before GitLab is wired. Sample pipeline ids stay at `900001` and above so a re-import does not delete real rows.

**Done when:** with a few inserted rows, `/api/releases` includes dev and `/api/releases/audit` does not.

## 11. `app/pipelines.py`

`GET /api/pipelines/{pipeline_id}`.

If the id is below 1, or `portal_started_pipeline` is false, return 404 and do not call GitLab. Otherwise read the status, store it, and when it is `success`, `failed`, `canceled`, `skipped`, or `unknown`, clear the environment lock for that id.

**Done when:** an id you never recorded returns 404, and an id you did record is the only one that reaches `gitlab_client`.

## 12. `app/trigger.py`

`POST /api/trigger-deployment`. This is the step that ties the model, the ledger, and GitLab together.

1. Read the username from the session. Audit the attempt.
2. `reserve_environment` for that environment. A fresh `reserving` lock older than three minutes can be dropped first. If a lock or the latest ledger row still has a non-terminal status, ask GitLab. A 404 becomes `unknown` and does not block forever. Anything else still running returns 409 with the pipeline number.
3. Call `trigger_pipeline`.
4. On the new id, `note_pipeline_running` with the same reservation token. Then `record_release`.
5. If GitLab accepted the call and the ledger write fails, return 500 telling the operator not to retry. Leave the lock in place.
6. If the failure happens before a pipeline id exists, release only this request's reservation.
7. Return `pipelineId` and the orchestrator pipelines URL when that URL is `http` or `https`. Do not return GitLab's raw JSON.

**Done when:** two overlapping dev triggers produce one GitLab call and one 409, and a prod body without a change request never reaches GitLab.

## 13. `main.py`

Create the FastAPI app with the lifespan from `app.startup`. Turn off `/docs`, `/redoc`, and `/openapi.json`.

Call `install_middleware`. Include the trigger, pipeline, and releases routers. Mount `public/` last, with HTML enabled, so those routes are not swallowed by the static files.

The process command is `uvicorn main:app --host 0.0.0.0 --port 8000`.

**Done when:** the process starts, `/login` is 200, and `/docs` redirects to the login page.

## 14. The two pages

`public/index.html` is the deploy form. It reads `/api/me` for the username. The form posts the `DeploymentRequest` fields. The popup opens immediately. Poll `/api/pipelines/{id}` every few seconds for about four minutes. Play the rocket only when the status is `success`. Failure, cancel, and skip leave the rocket alone and show a message. A 409 shows the server's detail text as-is.

`public/releases.html` draws the chart from `/api/releases` and the production table from `/api/releases/audit`. Put text on the page with `textContent`. A pipeline link is set only when the URL starts with `http://` or `https://`.

**Done when:** signed out, the page sends you to `/login`. Signed in, the form and the chart render, and a bad tenant name shows the validation message in the popup.

## 15. Run it in Docker

`Dockerfile` is a two-stage build. The runtime user is uid `10001`. Copy `ca.pem`, the Python modules, the `app` package, and `public/`. The ledger directory is `/data`.

`docker-compose.yaml` runs Keycloak and the portal. Keycloak imports `keycloak/realm-export.json`, which has the group `dev-portal-users`, the client `dev-portal`, and two users: `devportal-user` in the group, `outsider-user` outside it. Pin Keycloak's hostname to `localhost` so the token issuer is the same for the browser and for the portal container.

The portal's GitLab URL is `http://host.docker.internal:8929`, because `localhost` inside the container is the portal itself. The pipelines link shown to the operator is `http://localhost:8929/...`. `PORTAL_PUBLIC_URL` is `http://localhost:8000`.

Put the real GitLab token in `mock-secrets/gitlab-token.local` and mount it with a gitignored override. The committed token file is a fake.

**Done when:** `docker compose up -d --build portal` serves `/login`, `devportal-user` reaches the form, and `outsider-user` is sent back with `forbidden`.

## 16. GitLab and the tenant jobs

`docker-compose.gitlab.yaml` adds GitLab CE on port 8929 and a runner. Do not use `docker compose down --remove-orphans`. That removes GitLab, because it comes from the second compose file.

GitLab is up when `http://localhost:8929/api/v4/version` returns 401.

`terraform/` creates the group `tenants`, the project `tenant_orchestrator`, and one project per tenant (atlas, borealis, cedar). It commits three files:

| Source | Lands in GitLab as |
|---|---|
| `terraform/pipeline/orchestrator-ci.yml` | orchestrator `.gitlab-ci.yml` |
| `terraform/pipeline/generate-downstream.sh` | `scripts/generate-downstream.sh` |
| `terraform/pipeline/gitlab-ci.yml` | each tenant's `.gitlab-ci.yml` |

The orchestrator ignores push pipelines. A portal trigger is an API pipeline, so it runs. `generate-downstream` checks the trigger name and the tenant names, quotes the variables, and writes one trigger job per tenant. `trigger-downstream` runs that file as a child pipeline and waits. Each tenant job echoes the variables, including `TRIGGERED_BY`.

**Done when:** a dev trigger for `atlas` shows a parent pipeline, a child with one trigger, and a tenant job whose log contains your Keycloak username.

## 17. EKS

Do this last. The chart in `helm/dev-portal` deploys only the portal. It creates a VirtualService for the Istio gateway that is already on the cluster. It does not create a Gateway or a Kubernetes Ingress.

One replica. The ledger is one SQLite file on a volume. Secrets are the GitLab token, the session secret, and the Keycloak client secret. The public URL follows `istio.host` and becomes `PORTAL_PUBLIC_URL`.

Follow [eks.md](eks.md) for the values file and the install command. The rewrite is done when `helm template` shows a VirtualService and no Ingress, and `/login` answers on the portal host.

## Where to look when you are stuck

| You are writing | Open |
|---|---|
| A setting or URL | `config.py` |
| "Should this JSON be accepted?" | `model.py` |
| A row, a lock, a CSV cell | `releases_store.py` and `tests/test_releases_store.py` |
| The GitLab HTTP call | `gitlab_client.py` |
| Sign-in, the group, logout | `auth.py` |
| "Why was this request refused before the route?" | `app/middleware.py` |
| The button | `app/trigger.py` and `public/index.html` |
| The spinner after the button | `app/pipelines.py` |
| The chart | `app/releases.py` and `public/releases.html` |
| Boot | `app/startup.py` and `main.py` |
| What GitLab does with the variables | `terraform/pipeline/` |

## The last check

From a fresh start of both compose files:

1. `outsider-user` is refused.
2. `devportal-user` opens the deploy form.
3. Trigger dev for `atlas`. The popup waits, then the rocket plays only after GitLab reports success.
4. The Releases page shows the new point. A production trigger without a change request never leaves the portal.
5. Trigger dev again while that pipeline is still running. The popup says a release is already in flight.
6. `GET /api/pipelines/1` is 404 when the ledger has no such id.

When those six are true, the rewrite matches this repo.
