# Install the dev portal on EKS with Helm

The chart in `helm/dev-portal` deploys the portal and nothing else. Browser traffic reaches it through the Istio gateway already on the cluster. The chart creates a VirtualService for that gateway. It does not create a Gateway, and it does not create a Kubernetes Ingress.

Keycloak and GitLab stay outside the cluster. The local Keycloak container in this repo is only for laptop testing.

## What you need first

- An EKS cluster with Istio installed, and a Gateway that already terminates TLS for the host you will use.
- A StorageClass that can create a 1Gi volume. The release ledger is one SQLite file on that volume.
- The portal image in a registry the nodes can pull, usually ECR.
- A Keycloak client in the company realm.
- A GitLab token that can create a pipeline on `tenant_orchestrator`.
- `kubectl` and `helm` pointed at the cluster.

Find the Gateway the chart should attach to:

```powershell
kubectl get gateway -A
```

Use the value as `namespace/name`, for example `istio-system/istio-ingressgateway`.

The namespace you install into should be part of the mesh if sidecars are required in this cluster. The usual label is `istio-injection=enabled`. The pod listens on port 8000 and its probes call `/login`.

## What the chart creates

| Object | Purpose |
|---|---|
| Deployment | One portal pod, user 10001, read-only GitLab token mount |
| Service | ClusterIP on port 8000 |
| PersistentVolumeClaim | The SQLite ledger at `/data` |
| ConfigMap | GitLab, Keycloak, and the public URL |
| Secret | GitLab token, session signing key, Keycloak client secret |
| ServiceAccount | No API token mounted into the pod |
| VirtualService | Sends the portal host on your existing Gateway to the Service |
| ServiceEntry | Optional. Only when the mesh blocks unregistered outbound hosts |

`portal.replicaCount` has to stay at 1. Two pods would each have their own idea of the SQLite file. The Deployment uses the Recreate strategy so the old pod releases the volume before the new one starts.

## Keycloak client

Create a confidential client. Set the redirect URIs to the public portal origin:

- `https://portal.example.com/auth/callback`
- Post-logout redirect `https://portal.example.com/login`

The browser uses `keycloak.externalUrl`. The pod uses `keycloak.internalUrl` for the code exchange. Those can be the same host when the pod can reach the public Keycloak address.

The portal grants a session when the userinfo `groups` claim contains `keycloak.requiredGroup`. Point that at the group your company already uses for this app. The client needs a group-membership mapper that puts that group in the `groups` claim. Leave `requiredGroup` empty only if every authenticated user should get in.

## Values file

Copy the example and fill it in. `values-local.yaml` is gitignored. Do not commit the token or the client secret.

```powershell
Copy-Item helm\dev-portal\values-example.yaml helm\dev-portal\values-local.yaml
```

`helm/dev-portal/values-example.yaml` shows every field. The ones that have to match your cluster:

| Field | What to put |
|---|---|
| `portal.image.repository` and `portal.image.tag` | The image you pushed |
| `portal.gitlab.baseUrl` | API host the pod can call, no trailing slash |
| `portal.gitlab.projectId` | `root/tenant_orchestrator`, or a numeric id |
| `portal.gitlab.pipelinesUrl` | Page the operator's browser opens after a success |
| `keycloak.externalUrl` | Keycloak as the browser sees it |
| `keycloak.internalUrl` | Keycloak as the pod sees it |
| `keycloak.realm`, `clientId`, `requiredGroup` | The company client |
| `istio.gateway` | `namespace/name` from `kubectl get gateway -A` |
| `istio.host` | Hostname on that Gateway, such as `portal.example.com` |
| `istio.tls` | `true` when the Gateway serves HTTPS. The pod still receives HTTP |
| `secrets.gitlabToken` | GitLab personal or project token |
| `secrets.sessionSecret` | A unique random string, at least 16 characters |
| `secrets.keycloakClientSecret` | The confidential client secret |

When `istio.tls` is true, the chart sets `SESSION_COOKIE_SECURE=true` and `PORTAL_PUBLIC_URL` to `https://<istio.host>`. An HTTPS portal refuses to start if the session secret is still the local default `dev-session-secret-change-me`.

`portal.gitlab.baseUrl` and `portal.gitlab.pipelinesUrl` are different on purpose. The first is the API the pod calls. The second is the link shown to the operator. On a laptop those are `http://host.docker.internal:8929` and `http://localhost:8929/...`. On EKS, set each to the address that belongs to that side.

If the mesh outbound policy is `REGISTRY_ONLY`, the pod cannot call Keycloak or GitLab until those hosts are registered. Set:

```yaml
istio:
  serviceEntries: true
  externalHosts:
    - keycloak.example.com
    - gitlab.example.com
```

Leave `serviceEntries` false when the mesh already allows external HTTPS.

## Install

Build and push the image first. The chart does not build it.

```powershell
helm upgrade --install dev-portal ./helm/dev-portal `
  --namespace dev-portal --create-namespace `
  -f helm/dev-portal/values-local.yaml
```

Check the render before you install:

```powershell
helm template dev-portal ./helm/dev-portal -f helm/dev-portal/values-local.yaml
```

You should see a VirtualService whose `gateways` list is your existing Gateway, and no Ingress.

Point DNS for `istio.host` at the Istio ingress address:

```powershell
kubectl get svc -n istio-system
```

Use the external address of the ingress gateway Service. The chart does not allocate a load balancer of its own.

## Check that it came up

```powershell
kubectl -n dev-portal rollout status deploy
kubectl -n dev-portal get virtualservice,pods,pvc
```

Open `https://portal.example.com/login`. You should see the sign-in page. Sign in with an account in the required group, open Deploy, and trigger a dev release for one tenant. The popup should reach a pipeline id, and the GitLab link should open `portal.gitlab.pipelinesUrl`.

The pod is ready when `/login` returns 200. That path is public, so the probes do not need a session.

## Upgrade

Push a new image tag, set `portal.image.tag`, and run the same `helm upgrade --install` command. The Recreate strategy stops the old pod before the new one mounts the ledger volume. A config or secret change rolls the pod as well, because those files are checksummed onto the pod template.

Roll back with:

```powershell
helm -n dev-portal rollback dev-portal
```

The ledger file stays on the PVC across a rollback.

## Values the chart will refuse

- `portal.replicaCount` other than 1.
- A missing image repository or tag.
- A missing GitLab base URL, Keycloak URL, realm, or client id.
- A missing `istio.host` or `istio.gateway` while `istio.enabled` is true and `portal.publicUrl` is empty.
- A session secret shorter than 16 characters.
- `istio.serviceEntries: true` with an empty `istio.externalHosts` list.
