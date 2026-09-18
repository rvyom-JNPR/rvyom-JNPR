# redis-wlc CPU/Slowlog Investigation Runbook (gcp-production-1)

## Background

- **Reporter:** Nicolas Dade — asked for a sample of queries hitting the original
  non-clustered `redis-wlc` Memorystore instance, since WLC currently double-reads from
  both the legacy standalone instance and the new sharded/clustered instance during
  startup migration.
- **Goal:** Capture `SLOWLOG`, `INFO commandstats`, `INFO cpu`, and a short `MONITOR`
  traffic sample from `redis-wlc` in **gcp-production-1** to hand off for RCA on CPU/
  memory growth.

## Environment

- **GCP project:** `mist-host-prod`
- **Region:** `us-west2`
- **Memorystore instance:** `redis-wlc`
- **Host:reachable via VPC:** `10.6.0.36:6379`
- **Access path:** exec `redis-cli` from a pod inside the cluster's VPC (predixy pods do
  **not** ship `redis-cli`, so use a temporary debug pod with a `redis` image instead).

## Steps

### 1. Locate the Memorystore instance host/port

```bash
gcloud redis instances describe redis-wlc \
  --project mist-host-prod --region us-west2 \
  --format='value(host,port)'
# -> 10.6.0.36  6379
```

If you don't already know the project, loop over the known GCP prod projects/regions:

```bash
projects="mist-adp-prod mist-aiops-prod mist-dataproc-prod mist-freestyle-prod mist-host-prod mist-k8s-prod mist-platform-prod"
regions="us-west1 us-west2 us-central1 us-east1 us-east4 europe-west1 europe-west4"
for p in $projects; do
  for r in $regions; do
    out=$(gcloud redis instances list --project "$p" --region "$r" --format="value(name,host)" 2>/dev/null)
    [ -n "$out" ] && { echo "== $p / $r =="; echo "$out"; }
  done
done
```

### 2. Spin up a temporary debug pod (predixy images lack `redis-cli`)

Run each command from its own pod (or reuse one non-interactively) in the
`predixy-general-cluster` namespace so it has VPC access to the Memorystore host:

```bash
# Slowlog (top slow commands)
kubectl run redis-debug --rm -it --restart=Never --image=redis:7.0-alpine \
  -n predixy-general-cluster -- redis-cli -h 10.6.0.36 -p 6379 SLOWLOG GET 128

# Command stats (CPU cost by command type)
kubectl run redis-debug2 --rm -it --restart=Never --image=redis:7.0-alpine \
  -n predixy-general-cluster -- redis-cli -h 10.6.0.36 -p 6379 INFO commandstats

# General info/CPU
kubectl run redis-debug3 --rm -it --restart=Never --image=redis:7.0-alpine \
  -n predixy-general-cluster -- redis-cli -h 10.6.0.36 -p 6379 INFO cpu

# Live traffic sample (~20-30s)
kubectl run redis-debug4 --rm -it --restart=Never --image=redis:7.0-alpine \
  -n predixy-general-cluster -- redis-cli -h 10.6.0.36 -p 6379 MONITOR \
  > redis-wlc-monitor-$(date +%Y%m%d-%H%M%S).log
```

> **Note:** `kubectl exec` into an existing `predixy-*` pod will fail with
> `exec: "redis-cli": executable file not found in $PATH` — predixy images don't bundle
> a redis client, hence the disposable debug pod above.

### 3. Stop the MONITOR capture

`MONITOR` streams indefinitely until interrupted. If Ctrl+C doesn't register in the
terminal (stdout redirected to a file can eat the interrupt), force-delete the pod from
a second terminal instead:

```bash
kubectl delete pod redis-debug4 -n predixy-general-cluster --grace-period=0 --force
```

Then verify what was captured:

```bash
ls -la redis-wlc-monitor-*.log
wc -l redis-wlc-monitor-*.log
head -20 redis-wlc-monitor-*.log
```

### 4. Upload results for the investigation

```bash
gcloud storage cp redis-wlc-slowlog-<timestamp>.log \
  gs://mist-redis-backup-production/investigation/
gcloud storage cp redis-wlc-monitor-<timestamp>.log \
  gs://mist-redis-backup-production/investigation/
```

Share the resulting `gs://mist-redis-backup-production/investigation/...` path(s) with
the requester (e.g., Nicolas Dade) along with a short summary (e.g., slowlog entry
count, top commands by CPU from `commandstats`, and whether MONITOR shows abnormal
traffic volume vs. genuinely slow commands).

## Gotchas

- `$POD`/`$NS` shell variables must actually be exported before use — if unset, an
  inline `-p 6379` after `-n` can get swallowed by `kubectl exec` and produce
  `Error: unknown shorthand flag: 'p' in -p`. Prefer fully inlining pod/namespace names
  to avoid this.
- This session's tooling had no `gcloud` SDK/credentials configured, so all `gcloud`
  and `kubectl` commands had to be run manually by the on-call engineer, not by the
  assistant.
