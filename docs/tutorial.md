# How the dev portal works

This is the tour of the release tool in this repo: what it is for, what happens after you press Trigger pipeline, and where each decision lives in the code.

The picture with the sequence diagrams is [architecture.md](architecture.md). The EKS and Helm steps are [eks.md](eks.md).

## What you are looking at

The portal is a small web app. A signed-in operator picks an environment, one or more tenants, and a pipeline, then starts a release. Production also asks for a change-request number.

The app does not deploy tenants itself. It asks one GitLab project, `tenant_orchestrator`, to start the work. That project writes a short pipeline file and uses it to start the tenant projects you named, such as `tenants/atlas`.

Three records stay separate:

| Question | Who answers |
|---|---|
| Who was allowed to open the app? | Keycloak, via the group on the account |
| Who pressed the button, and which change request covered production? | The portal ledger |
| Did the jobs succeed, and what did they print? | GitLab |

Locally, Keycloak and GitLab run in Docker so you can click through the whole path. On EKS you deploy only the portal. Company Keycloak and company GitLab stay where they already are, and Istio sends browser traffic to the pod.

## A release, from the button to the tenant log

1. You open the portal and land on the sign-in page. Continue with Keycloak. If your account is in the `dev-portal-users` group, you get a session and the Deploy form. If it is not, you come back to the sign-in page and no session is created. Locally the working account is `devportal-user` / `devportal-pass`. `outsider-user` / `outsider-pass` is the account that should be refused.

2. You choose Dev, Test, Pre-prod, or Prod. Prod opens an extra panel. You tick "Allow this production trigger" and type a change request such as `CHG0012345`. Dev, test, and pre-prod throw a change-request number away even if one is sent.

3. You type tenants as a comma-separated list, for example `atlas, borealis`. You pick Tenant baseline, Kasm VDI, or Tenant services. "Replace existing resources" reveals the extra field for that pipeline: a Terraform address, a replace state, or a set of services.

4. Trigger pipeline opens the popup immediately, with a spinner. The words change from "Sending the trigger…" to "Pipeline #N is running." The portal is polling GitLab every few seconds.

5. When GitLab reports success, the spinner goes away, a rocket plays, and the popup comes back with two links: release history, and the tenant orchestrator pipelines page. If the pipeline fails, is canceled, or is skipped, the rocket stays put and the popup says the pipeline failed. If it is still running after about four minutes, the popup tells you to look in GitLab.

6. In GitLab, open that orchestrator pipeline. `generate-downstream` prints the variables and writes `generated-ci.yml`. `trigger-downstream` runs that file as a child pipeline and waits. The child has one trigger per tenant. Each tenant pipeline's `hello-world` job prints the same variables, including `TRIGGERED_BY` set to your Keycloak username.

That last line is how you tell a portal release from anything else. The username in the job log is the person who was signed in.

## Why the portal calls only one project

The form never picks a GitLab project. `gitlab_client.py` always posts to the project in `GITLAB_PROJECT_ID`, default `root/tenant_orchestrator`, on branch `main`. The tenants you typed travel in `TENANT_LIST`.

`scripts/generate-downstream.sh` splits that list and writes a trigger job whose project is `tenants/<name>`. Atlas is started only when atlas is in the list. The script drops a blank entry and a repeated name, and it stops if a name is not a plain identifier.

A push to the orchestrator does not start this. The workflow rule in `terraform/pipeline/orchestrator-ci.yml` ignores push pipelines. The portal uses the GitLab API, so those pipelines run.

## What the portal refuses before GitLab sees it

`model.py` is the gate on the JSON body.

- Environment and pipeline are fixed lists.
- A tenant name is letters, numbers, `_`, or `-`. At most 20 names.
- A baseline replace target looks like `module.database`. Quotes, spaces, and shell characters are rejected.
- Kasm replace state is the same kind of plain token. The form starts it at `KASM`.
- Services have to be one of: airflow, cloudbeaver, gitlab_runner, immuta, jupyterhub, mlflow, s3uploader, superset.
- With replace turned off, those extra fields are cleared so they are not forwarded.
- Production without the checkbox, or without a change-request number, is rejected.

The same shape is checked again in the orchestrator script before it writes YAML. A value with a quote or a control character stops the generator instead of becoming a broken pipeline file.

## One release at a time, per environment

Dev can be running while test is running. Dev cannot have two releases in flight.

Before the portal calls GitLab it takes a hold on that environment. The hold has a random token that belongs to that request. When GitLab returns a pipeline id, the hold is marked running. When the status becomes success, failed, canceled, skipped, or unknown, the hold is cleared and the environment can be used again.

If you click twice, the second popup says a release is already in flight and names the pipeline. If the first request dies before GitLab answers, the hold expires after three minutes and the next click can proceed. A later request cannot delete a hold it did not create.

The status URL only works for a pipeline id that is already in the ledger. Asking for some other id returns "not found".

## The ledger and the Releases page

After GitLab accepts the pipeline, the portal inserts one SQLite row: who, when, environment, tenants, pipeline, GitLab id, and for production the change request. The row is the audit record. GitLab remains the place to read logs.

If GitLab accepts the pipeline and the ledger write then fails, the portal says so and tells you not to retry. Retrying would start a second pipeline.

The Releases chart draws every environment. The table and the CSV download contain production rows only. A cell that would look like a spreadsheet formula is prefixed so Excel treats it as text.

Sample rows live in `fixtures/sample-releases.json` (pipeline ids 900001–900018). `python fixtures/import_sample_releases.py` loads them. Loading again replaces those ids and leaves real releases in place.

## Sign-in, in a bit more detail

`auth.py` runs the authorization-code flow.

The landing page is `/login`. The button goes to `/login/keycloak`, which stores a random `state` and redirects the browser to Keycloak. `/auth/callback` checks that state, exchanges the code, and reads userinfo. The Keycloak client secret is used only on that server-side call.

The session cookie is signed. It holds `preferred_username` and `email`. It does not hold the access token. Logout uses `PORTAL_PUBLIC_URL`, so the return address is the one configured for the portal.

On your machine the group check looks at the `groups` claim and expects `dev-portal-users`. In the company Keycloak, set `KEYCLOAK_REQUIRED_GROUP` to the group you already use for this app. People outside that group never get a session. The portal does not add a second role for production. Production is the same sign-in, plus the checkbox and the change request.

Local Keycloak is for this repo only. It runs `start-dev` and imports `keycloak/realm-export.json`. The admin user is `admin` / `admin`. That container is not part of the EKS chart.

## Where each behavior lives

| What you see | File |
|---|---|
| Deploy form, spinner, rocket, popup | `public/index.html` |
| Releases chart, production table, CSV link | `public/releases.html` |
| Sign-in page and Keycloak callback | `auth.py` |
| App wiring | `main.py` |
| Token load and ledger startup | `app/startup.py` |
| Sign-in gate and security headers | `app/middleware.py` |
| Trigger and the one-in-flight lock | `app/trigger.py` |
| Pipeline status polling | `app/pipelines.py` |
| Release chart, production ledger, CSV | `app/releases.py` |
| Field rules | `model.py` |
| GitLab create and status calls | `gitlab_client.py` |
| Ledger and the per-environment hold | `releases_store.py` |
| URLs, secrets, session lifetime | `config.py` |
| Orchestrator pipeline | `terraform/pipeline/orchestrator-ci.yml` |
| Tenant fan-out script | `terraform/pipeline/generate-downstream.sh` |
| Tenant hello-world job | `terraform/pipeline/gitlab-ci.yml` |
| EKS chart | `helm/dev-portal/` |

`config.py` is the list of knobs. Environment variables override every one of them. The ones you change between a laptop and EKS are the GitLab base URL, the orchestrator pipelines URL, the Keycloak URLs, the realm and client, the public portal URL, and the session secret.

## Run it on your machine

GitLab takes a few minutes to become ready after Docker starts. The API is up when `http://localhost:8929/api/v4/version` returns 401. That 401 means GitLab heard you and wants a token.

```powershell
docker compose -f docker-compose.yaml -f docker-compose.gitlab.yaml up -d --build portal
```

Open http://localhost:8000. Sign in as `devportal-user`. Trigger a dev release for `atlas`. Wait for the rocket. Then open the pipelines link in the popup and the Releases page.

The portal image copies the Python files and `public/` at build time. After a code change, run the same compose command again so the container picks up the new image.

Ledger tests, with no Docker:

```powershell
python -m unittest tests.test_releases_store -v
```

## When a click does not launch

| What the popup says | What it means |
|---|---|
| Failed to connect to GitLab | The portal cannot reach `GITLAB_BASE_URL`. Locally, GitLab is down or still starting. |
| GitLab rejected the pipeline trigger request | GitLab answered, and the token or the project path is wrong, or `main` is missing. |
| A dev release is already in flight | That environment has a pipeline that has not finished. Wait, or look up that pipeline id. |
| Pipeline #N failed | GitLab finished the orchestrator pipeline with failed, canceled, or skipped. Open the job log. |
| Pipeline #N is still running | Four minutes passed. The pipeline is still going. Use the GitLab link. |
| GitLab accepted the pipeline but it was not written to the release ledger | The pipeline exists. Do not click again until the ledger is healthy. |
