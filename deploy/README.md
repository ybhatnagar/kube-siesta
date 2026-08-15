# Deployment

Container images and a Helm chart for running the Kube Siesta in a cluster:

- **collector** CronJob (scheduled ingestion) + a **trigger-service** Deployment for
  on-demand collection (the engine's `POST /collections` calls it),
- **engine** Deployment + Service (analysis + REST API),
- **ui** Deployment + Service (optional static front-end),
- an optional bundled **Postgres** (demo) or an external database,
- a **migrate** hook that applies the schema, and read-only **RBAC** for the collector.

The tool only ever *reads* from the target cluster — nothing it deploys writes to your
workloads.

## Images

Each module has its own small image (build from the repo root):

```bash
docker build -t kubesiesta/collector:0.1.0 collector/   # ~26 MB (distroless, static Go)
docker build -t kubesiesta/engine:0.1.0    engine/      # engine + API + Postgres driver
docker build -t kubesiesta/ui:0.1.0        ui/          # ~76 MB (nginx, static bundle)
```

Push them to a registry your cluster can pull from, and set the `images.*.repository`
values accordingly. For a **local cluster**, load them into the nodes instead (the chart
uses `imagePullPolicy: IfNotPresent`, so no registry is needed):

```bash
# minikube
minikube image load kubesiesta/collector:0.1.0 kubesiesta/engine:0.1.0 kubesiesta/ui:0.1.0

# kind (standalone CLI)
kind load docker-image kubesiesta/collector:0.1.0 kubesiesta/engine:0.1.0 kubesiesta/ui:0.1.0

# kind managed by Docker Desktop (no kind CLI) — import into each worker node's containerd
for n in $(kubectl get nodes -o name | sed 's|node/||' | grep -v control-plane); do
  docker save kubesiesta/collector:0.1.0 kubesiesta/engine:0.1.0 kubesiesta/ui:0.1.0 | docker exec -i "$n" ctr -n k8s.io images import -
done
```

> After changing code, rebuild the image and reload it, then `kubectl rollout restart`
> the affected Deployment (same tag + `IfNotPresent` means nodes keep the old image
> until you replace it).

**Verified** on a local 3-node kind cluster (Kubernetes 1.36): migrate hook → schema
applied (including the polymorphic `analysis_runs.run_type` + the three
`maintenance_*` tables); engine, UI, and Postgres healthy; the full seed → run →
recommendations flow through the UI's `/api` proxy for **both** the job flow
(idle-workload → CronJob / KEDA / …, with peers) **and** the maintenance flow
(target + upstream deps → a lowest-impact downtime window); and the collector Job
writing a `collection_runs` row and degrading gracefully when Prometheus is absent.

## Install (bundled Postgres — quickest)

```bash
helm install jr deploy/helm/kubesiesta \
  --namespace kubesiesta --create-namespace \
  --set collector.promUrl=http://prometheus.monitoring:9090
```

This brings up Postgres, runs the schema migration, starts the engine + UI, and schedules
the collector hourly. Point `collector.promUrl` at your Prometheus. Then:

```bash
kubectl -n kubesiesta port-forward svc/ks-kubesiesta-ui 8080:80
# open http://localhost:8080/  (the UI proxies /api to the engine)
```

## Install (external database — production)

Don't ship the bundled Postgres to production. Provide a Secret with a ready-made DSN:

```bash
kubectl -n kubesiesta create secret generic kubesiesta-db \
  --from-literal=dsn='postgres://user:pass@pg.internal:5432/kubesiesta?sslmode=require'

helm install jr deploy/helm/kubesiesta -n kubesiesta \
  --set postgres.enabled=false \
  --set database.existingSecret=kubesiesta-db \
  --set collector.promUrl=http://prometheus.monitoring:9090 \
  --set engine.corsOrigins=https://kubesiesta.your-domain \
  --set ingress.enabled=true --set ingress.host=kubesiesta.your-domain --set ingress.className=nginx
```

With an Ingress, `/` serves the UI and `/api` the engine on one host.

## Try it on minikube (no Prometheus needed)

```bash
minikube start
minikube image load kubesiesta/collector:0.1.0
minikube image load kubesiesta/engine:0.1.0
minikube image load kubesiesta/ui:0.1.0

helm install jr deploy/helm/kubesiesta -n kubesiesta --create-namespace
kubectl -n kubesiesta rollout status deploy/ks-kubesiesta-engine

# There's no Prometheus to collect from, so seed the synthetic demo cluster straight
# into Postgres (the schema is already applied by the migrate hook):
DSN=$(kubectl -n kubesiesta get secret ks-kubesiesta-db -o jsonpath='{.data.dsn}' | base64 -d)
kubectl -n kubesiesta run seed --rm -it --restart=Never --image=kubesiesta/engine:0.1.0 \
  --image-pull-policy=IfNotPresent -- \
  synth --seed-db --db-driver postgres --db-dsn "$DSN" --out /tmp/synth.json

kubectl -n kubesiesta port-forward svc/ks-kubesiesta-ui 8080:80
# open http://localhost:8080/ → pick the "synth" cluster → run → recommendations
```

## Collect on demand

The collector runs on `collector.schedule` (hourly by default). To run it right now:

```bash
kubectl -n kubesiesta create job --from=cronjob/ks-kubesiesta-collector collect-now
```

## Key values

| Value | Default | Notes |
|---|---|---|
| `images.*.repository` / `.tag` | `kubesiesta/*` `0.1.0` | set to your registry |
| `postgres.enabled` | `true` | bundled demo DB; set `false` for prod |
| `database.existingSecret` | `""` | Secret with a `dsn` key (prod) |
| `collector.schedule` | `0 * * * *` | ingestion cron |
| `collector.promUrl` | `http://prometheus.monitoring:9090` | your Prometheus |
| `collector.resources` | `cpu,memory` | also `net_tx,net_rx,ephemeral_storage` |
| `engine.corsOrigins` | `*` | lock down in prod |
| `ui.enabled` | `true` | disable to run headless |
| `ingress.enabled` | `false` | routes `/`→UI, `/api`→engine |

## Validate the chart locally

```bash
helm lint deploy/helm/kubesiesta
helm template jr deploy/helm/kubesiesta | kubeconform -strict -kubernetes-version 1.30.0
```

## Notes
- Images run as **non-root** with dropped capabilities; the engine and collector use a
  read-only root filesystem.
- Cluster credentials referenced by `credential_ref` are expected to live in Kubernetes
  Secrets (the DB never stores raw credentials). Wiring live cluster discovery to those
  Secrets lands with the (not-yet-built) Kubernetes client.
