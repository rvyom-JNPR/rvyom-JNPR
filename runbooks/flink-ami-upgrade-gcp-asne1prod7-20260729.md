# Flink GKE Upgrade Runbook — asne1prod7-gcp — 2026-07-29

## Summary

| Field | Value |
|---|---|
| Environment | `asne1prod7-gcp` (Commercial Production GCP Japan) |
| GKE Cluster | `asne1prod7-2-green` (asia-northeast1) |
| kubectl context | `gcp-asne1prod7-2-green` |
| IAC path | `asne1prod7-gcp/auto/gke-2-green/gke_config.hcl` |
| Old version | `1.33.11-gke.1197000` (black) |
| New version | `1.33.13-gke.1011000` (red, GKE auto-upgraded to cluster master) |
| PR | [mistsys/iac#4296](https://github.com/mistsys/iac/pull/4296) |
| Jira | TBD |
| Date | 2026-07-29 |
| Operator | rvyom |

---

## Flink Clusters

| Namespace | URL | Type |
|---|---|---|
| flink-la-12xlarge-1-green | flink-la-12xlarge-1-green-asne1prod7.mist.pvt | la primary |
| flink-la-12xlarge-1-backup-green | flink-la-12xlarge-1-backup-green-asne1prod7.mist.pvt | la backup |
| flink-la-6xlarge-1-green | flink-la-6xlarge-1-green-asne1prod7.mist.pvt | la primary |
| flink-la-6xlarge-1-backup-green | flink-la-6xlarge-1-backup-green-asne1prod7.mist.pvt | la backup |
| flink-la-6xlarge-2-green | flink-la-6xlarge-2-green-asne1prod7.mist.pvt | la primary |
| flink-la-6xlarge-2-backup-green | flink-la-6xlarge-2-backup-green-asne1prod7.mist.pvt | la backup |
| flink-la-8xlarge-1-green | flink-la-8xlarge-1-green-asne1prod7.mist.pvt | la primary |
| flink-la-8xlarge-1-backup-green | flink-la-8xlarge-1-backup-green-asne1prod7.mist.pvt | la backup |
| flink-la-8xlarge-2-green | flink-la-8xlarge-2-green-asne1prod7.mist.pvt | la primary |
| flink-la-8xlarge-2-backup-green | flink-la-8xlarge-2-backup-green-asne1prod7.mist.pvt | la backup |
| flink-app-8xlarge-1-green | flink-app-8xlarge-1-green-asne1prod7.mist.pvt | app primary |
| flink-app-8xlarge-1-backup-green | flink-app-8xlarge-1-backup-green-asne1prod7.mist.pvt | app backup |

**Job counts at start:**
- LA: monitored via TM pod counts (mistcli migration-status has JSON parse error for asne1prod7)
- App: monitored via TM pod counts

---

## Phase 1 — Discovery

Active color: **black** (`flink-jobmanager-black`, `flink-taskmanager-black` @ `1.33.11-gke.1197000`)
Cluster master version: `1.33.13-gke.1011000` (GKE will auto-upgrade new red pools to this)

Node counts at start:
- taskmanager-black: 23 nodes
- jobmanager-black: 6 nodes

---

## Phase 2 — Create Red Node Pools

**Branch**: `flink-nodepool/asne1prod7-red-20260729` (cut from `origin/main`)

> ⚠️ **Note**: Unlike mec1prod6, asne1prod7's `gke_config.hcl` did NOT have `flink-*-red` entries pre-defined in labels, taints, or oauth_scopes. These were added as part of this commit.

Added to `gke_config.hcl`:
- `flink-jobmanager-red` — n2d-standard-8, max 20, version `1.33.13-gke.1011000`
- `flink-taskmanager-red` — e2-highmem-8, max 48, version `1.33.13-gke.1011000`
- Labels, taints, oauth_scopes for both red pools

**PR**: TBD

---

## Phase 2b — Terragrunt Apply

Expected plan: `Plan: 4 to add, 0 to change, 0 to destroy`

Resources to create:
- `google_container_node_pool.pools["flink-jobmanager-red"]`
- `google_container_node_pool.pools["flink-taskmanager-red"]`
- `random_id.name["flink-jobmanager-red"]`
- `random_id.name["flink-taskmanager-red"]`

```bash
export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token \
  --impersonate-service-account terraform-create-asne1prod7@mist-infrastructure-iam.iam.gserviceaccount.com)
cd ~/workspace/iac/asne1prod7-gcp/auto/gke-2-green
terragrunt plan
terragrunt apply
```

Result: TBD

---

## Phase 3 — Verify

```bash
kubectl --context gcp-asne1prod7-2-green get nodes -l node_pool=flink-jobmanager-red
kubectl --context gcp-asne1prod7-2-green get nodes -l node_pool=flink-taskmanager-red
```

New red pools visible in GKE. 0 nodes (min=0, inflate not yet applied). ✅

---

## Phase 4 — Cordon Old Black Nodes

```bash
kubectl --context gcp-asne1prod7-2-green get nodes -l role=flink-taskmanager -o name | xargs kubectl --context gcp-asne1prod7-2-green cordon
kubectl --context gcp-asne1prod7-2-green get nodes -l role=flink-jobmanager -o name | xargs kubectl --context gcp-asne1prod7-2-green cordon
```

All 23 taskmanager-black and 6 jobmanager-black nodes: `Ready,SchedulingDisabled`

---

## Phase 5 — Inflate Pods

Inflate manifest location: `~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/`

- `flink-jobmanager-red.yaml` — replicas: 6
- `flink-taskmanager-red.yaml` — replicas: 23

```bash
kubectl --context gcp-asne1prod7-2-green apply -f ~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/flink-jobmanager-red.yaml
kubectl --context gcp-asne1prod7-2-green apply -f ~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/flink-taskmanager-red.yaml
```

---

## Phase 6 — Cluster URLs

```bash
kubectl --context gcp-asne1prod7-2-green get ingress -A \
  -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.spec.rules[0].host}{"\n"}{end}' \
  | grep -E 'flink-la|flink-app' | sort
```

---

## Phase 7 — Migrate Jobs to Backup

```bash
mistcli flink operator la asne1prod7 green move-all-jobs   # RequestID: TBD
mistcli flink operator app asne1prod7 green move-all-jobs  # RequestID: TBD
```

---

## Phase 8 — Monitor

```bash
mistcli flink operator la asne1prod7 green migration-status
mistcli flink operator app asne1prod7 green migration-status
```

| Time | LA primary | LA backup | App primary | App backup |
|---|---|---|---|---|
| T+0 | TBD | TBD | TBD | TBD |
| T+complete | 0 | TBD | 0 | TBD |

---

## Phase 9 — Restart Primary JobManagers

```bash
kubectl --context gcp-asne1prod7-2-green get pods -A -l component=jobmanager --no-headers \
  | grep -v backup | awk '{print $1, $2}' | while read ns pod; do
    node=$(kubectl --context gcp-asne1prod7-2-green get pod $pod -n $ns -o jsonpath='{.spec.nodeName}' 2>/dev/null)
    pool=$(kubectl --context gcp-asne1prod7-2-green get node $node -o jsonpath='{.metadata.labels.node_pool}' 2>/dev/null)
    if [[ "$pool" == "flink-jobmanager-black" ]]; then
      echo "Deleting $pod in $ns"
      kubectl --context gcp-asne1prod7-2-green delete pod $pod -n $ns
    fi
  done
```

---

## Phase 10 — Move Jobs Back to Primary

```bash
mistcli flink operator la asne1prod7 green move-all-jobs-back   # RequestID: TBD
mistcli flink operator app asne1prod7 green move-all-jobs-back  # RequestID: TBD
```

Final state:
- LA: TBD/TBD on primary ✅
- App: TBD/TBD on primary ✅

---

## Phase 11 — Drain Black Nodes

```bash
# Check if GKE CA already removed black TM nodes
kubectl --context gcp-asne1prod7-2-green get nodes -l role=flink-taskmanager --no-headers | awk '{print $2, $5}' | sort | uniq -c

# Drain remaining black nodes
kubectl --context gcp-asne1prod7-2-green get nodes -l role=flink-jobmanager --no-headers \
  | grep "1.33.11" | awk '{print $1}' \
  | xargs -I{} kubectl --context gcp-asne1prod7-2-green drain {} \
      --ignore-daemonsets --delete-emptydir-data --force
```

Scale down inflate pods:
```bash
kubectl --context gcp-asne1prod7-2-green scale deployment inflate-flink-taskmanager-red inflate-flink-jobmanager-red -n default --replicas=0
```

---

## Phase 12 — Cleanup

**Verify 0 black nodes in GCP console first:**
https://console.cloud.google.com/kubernetes/clusters/details/asia-northeast1/asne1prod7-2-green/nodes?project=mist-k8s-asne1prod7

**Remove from `gke_config.hcl`:**
- `flink-jobmanager-black` node pool block ✅
- `flink-taskmanager-black` node pool block ✅
- Labels, taints, oauth_scopes for both black pools ✅

**Terragrunt apply:** `Plan: 0 to add, 0 to change, 4 to destroy` ✅

> ⚠️ **Note**: First apply attempt was interrupted with Ctrl+C, leaving a stale state lock. Had to run `terragrunt force-unlock 1785363412932437` before re-applying.

**GKE node pool suffixes:**
- flink-jobmanager-red: `628f`
- flink-taskmanager-red: `d6d3`
- flink-jobmanager-black (deleted): `1d6b`
- flink-taskmanager-black (deleted): `9a86`

**PR commits:**
1. `feat(asne1prod7)`: Add flink-{jobmanager,taskmanager}-red node pools + labels/taints/oauth_scopes
2. `chore(asne1prod7)`: Remove flink-{jobmanager,taskmanager}-black node pools

---

## Issues Encountered & Resolutions

| # | Issue | Resolution |
|---|---|---|
| 1 | `flink-*-black` labels/taints/oauth_scopes not pre-defined in config | Added them as part of Phase 2 commit (unlike mec1prod6 which had both colors pre-defined) |
| 2 | `mistcli flink operator migration-status` returns JSON parse error | Used TM pod namespace distribution via `kubectl get pods` to monitor migration instead |
| 3 | Terragrunt apply interrupted with Ctrl+C, left stale state lock | Ran `terragrunt force-unlock 1785363412932437` to clear the lock, then re-applied |

---

## Key Commands Reference

```bash
ENV=asne1prod7
CONTEXT=gcp-${ENV}-2-green

export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token \
  --impersonate-service-account terraform-create-${ENV}@mist-infrastructure-iam.iam.gserviceaccount.com)

kubectl --context $CONTEXT get nodes -l role=flink-taskmanager --no-headers | awk '{print $2, $5}' | sort | uniq -c
kubectl --context $CONTEXT get nodes -l role=flink-jobmanager --no-headers | awk '{print $2, $5}' | sort | uniq -c

mistcli flink operator la ${ENV} green migration-status
mistcli flink operator app ${ENV} green migration-status
```
