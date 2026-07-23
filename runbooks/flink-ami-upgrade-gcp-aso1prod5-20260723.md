# Flink GKE Upgrade Runbook — aso1prod5-gcp — 2026-07-23

## Summary

| Field | Value |
|---|---|
| Environment | `aso1prod5-gcp` (Commercial Production GCP India) |
| GKE Cluster | `aso1prod5-2-green` (asia-south1) |
| kubectl context | `gcp-aso1prod5-2-green` |
| IAC path | `aso1prod5-gcp/auto/gke-2-green/gke_config.hcl` |
| Old version | `1.33.11-gke.1013000` (red) |
| New version | `1.33.12-gke.1270000` (black) |
| PR | [mistsys/iac#4245](https://github.com/mistsys/iac/pull/4245) |
| Jira | [MIST-208521](https://mistsys.atlassian.net/browse/MIST-208521) |
| Date | 2026-07-23 |
| Operator | rvyom |

---

## Flink Clusters

| Namespace | URL | Type |
|---|---|---|
| flink-la-6xlarge-1-green | flink-la-6xlarge-1-green-aso1prod5.mist.pvt | la primary |
| flink-la-6xlarge-1-backup-green | flink-la-6xlarge-1-backup-green-aso1prod5.mist.pvt | la backup |
| flink-la-6xlarge-2-green | flink-la-6xlarge-2-green-aso1prod5.mist.pvt | la primary |
| flink-la-6xlarge-2-backup-green | flink-la-6xlarge-2-backup-green-aso1prod5.mist.pvt | la backup |
| flink-la-8xlarge-1-green | flink-la-8xlarge-1-green-aso1prod5.mist.pvt | la primary |
| flink-la-8xlarge-1-backup-green | flink-la-8xlarge-1-backup-green-aso1prod5.mist.pvt | la backup |
| flink-la-8xlarge-2-green | flink-la-8xlarge-2-green-aso1prod5.mist.pvt | la primary |
| flink-la-8xlarge-2-backup-green | flink-la-8xlarge-2-backup-green-aso1prod5.mist.pvt | la backup |
| flink-la-12xlarge-1-green | flink-la-12xlarge-1-green-aso1prod5.mist.pvt | la primary |
| flink-la-12xlarge-1-backup-green | flink-la-12xlarge-1-backup-green-aso1prod5.mist.pvt | la backup |
| flink-app-8xlarge-1-green | flink-app-8xlarge-1-green-aso1prod5.mist.pvt | app primary |
| flink-app-8xlarge-1-backup-green | flink-app-8xlarge-1-backup-green-aso1prod5.mist.pvt | app backup |

**Job counts at start:**
- LA: 736 jobs across 5 primary clusters
- App: 18 jobs across 1 primary cluster

---

## Phase 1 — Discovery

Active color: **red** (`flink-jobmanager-red`, `flink-taskmanager-red` @ `1.33.11-gke.1013000`)

Node counts at start:
- taskmanager-red: 23 nodes
- jobmanager-red: 6 nodes

---

## Phase 2 — Create Black Node Pools

**Branch**: `flink-nodepool/aso1prod5-black-20260723-clean` (created from `origin/main`)

> ⚠️ **Issue encountered**: First branch was cut from `rvyom/MIST-206719-emr-iam-migration-aws-staging` instead of `main`, carrying 11 unrelated commits. Had to create a fresh branch from `origin/main` and cherry-pick only the flink commit.

Added to `gke_config.hcl`:
- `flink-jobmanager-black` — n2d-standard-8, max 24, version `1.33.12-gke.1270000`
- `flink-taskmanager-black` — n2d-standard-16, max 80, version `1.33.12-gke.1270000`

**PR**: [mistsys/iac#4245](https://github.com/mistsys/iac/pull/4245)

---

## Phase 2b — Terragrunt Apply

```
Plan: 4 to add, 0 to change, 0 to destroy
```
Resources created:
- `google_container_node_pool.pools["flink-jobmanager-black"]` → `flink-jobmanager-black-f608`
- `google_container_node_pool.pools["flink-taskmanager-black"]` → `flink-taskmanager-black-d4d8`
- `random_id.name["flink-jobmanager-black"]`
- `random_id.name["flink-taskmanager-black"]`

Applied successfully. Master version updated to `1.33.12-gke.1270000`.

**GKE node pool suffixes after apply:**
- jobmanager-black: `5c450b5c`
- taskmanager-black: `8a70f87f`

---

## Phase 3 — Verify

New black pools visible in GKE. 0 nodes (min=0, inflate not yet applied). ✅

---

## Phase 4 — Cordon Old Red Nodes

Cordoned using `role` label (not `cloud.google.com/gke-nodepool`):
```bash
kubectl --context gcp-aso1prod5-2-green get nodes -l role=flink-taskmanager -o name | xargs kubectl --context gcp-aso1prod5-2-green cordon
kubectl --context gcp-aso1prod5-2-green get nodes -l role=flink-jobmanager -o name | xargs kubectl --context gcp-aso1prod5-2-green cordon
```

All 23 taskmanager-red and 6 jobmanager-red nodes: `Ready,SchedulingDisabled` ✅

---

## Phase 5 — Inflate Pods

Inflate manifest location: `~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/`

Applied:
- `flink-jobmanager-black.yaml` — replicas: 7 (later doubled to 14)
- `flink-taskmanager-black.yaml` — replicas: 23

> **Note**: Replicas were set to match the existing red node counts. User requested doubling the jobmanager replicas to 14 mid-session. The manifest file was updated to reflect this.

Result: 30 inflate pods Pending → GKE CA provisioned 23 + 14 = 37 new black nodes over ~10 minutes. ✅

---

## Phase 5b — Init Pool Issue (autoscaler Pending)

**Problem**: `flink-la-autoscaler-556f74dd78-6bd6b` in `flink-la-8xlarge-1-backup-green` was Pending for ~90 minutes:

```
0/75 nodes available: 5 Insufficient memory, 1 max node group size reached
```

**Root cause**: All 5 `init-red` nodes were 80-85% memory allocated. Autoscaler needed 5G RAM. `init-red` pool was at `max_count=5` (at capacity).

**Fix**: Bumped `init-red` `max_count` from 5 → 7 in `gke_config.hcl`, committed + applied terragrunt. GKE CA provisioned a new init node and autoscaler scheduled within 2 minutes.

**Lesson**: Watch for non-flink pods (autoscalers, operators) going Pending after cordoning. They may need init pool capacity.

---

## Phase 6 — Cluster URLs

Discovered via:
```bash
kubectl --context gcp-aso1prod5-2-green get ingress -A \
  -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.spec.rules[0].host}{"\n"}{end}' \
  | grep -E 'flink-la|flink-app' | sort
```

12 clusters found (6 primary, 6 backup). See table above.

---

## Phase 7 — Migrate Jobs to Backup

```bash
mistcli flink operator la aso1prod5 green move-all-jobs   # RequestID: 2f1a4e20
mistcli flink operator app aso1prod5 green move-all-jobs  # RequestID: 3bcdf8ea
```

Both returned HTTP 202. ✅

> **Note**: `mistcli flink aso1prod5 jobs count` failed — "no built-in cluster list for env aso1prod5". Used `migration-status` instead.

---

## Phase 8 — Monitor

| Time | LA primary | LA backup | App primary | App backup |
|---|---|---|---|---|
| T+0 | 732 (99.5%) | 4 (0%) | 16 (88.9%) | 1 (5.6%) |
| T+30min | 194 (35.8%) | 542 (100%) | 0 | 18 (100%) |
| T+complete | 0 | 736 (100%) | 0 | 18 (100%) |

App migration completed before LA (fewer jobs). One job `client-stats-count-flapsdeployment` briefly in RECONCILING on backup — resolved automatically.

---

## Phase 9 — Restart Primary JobManagers

Checked primary jobmanager placement — some still on `flink-jobmanager-red`. Deleted them:

```bash
kubectl --context gcp-aso1prod5-2-green get pods -A -l component=jobmanager --no-headers \
  | grep -v backup | awk '{print $1, $2}' | while read ns pod; do
    node=$(kubectl --context gcp-aso1prod5-2-green get pod $pod -n $ns -o jsonpath='{.spec.nodeName}' 2>/dev/null)
    pool=$(kubectl --context gcp-aso1prod5-2-green get node $node -o jsonpath='{.metadata.labels.node_pool}' 2>/dev/null)
    if [[ "$pool" == "flink-jobmanager-red" ]]; then
      kubectl --context gcp-aso1prod5-2-green delete pod $pod -n $ns
    fi
  done
```

> ⚠️ Do NOT use `status` as a variable in zsh — it is read-only. Use `pstatus`.

6 primary jobmanagers restarted. All rescheduled on `flink-jobmanager-black`. ✅

---

## Phase 10 — Move Jobs Back to Primary

```bash
mistcli flink operator la aso1prod5 green move-all-jobs-back   # RequestID: a7cfc179
mistcli flink operator app aso1prod5 green move-all-jobs-back  # RequestID: ec50d7b4
```

Final state:
- LA: 736/736 on primary (100%) ✅
- App: 18/18 on primary (100%) ✅

---

## Phase 11 — Drain Red Nodes

User noticed 6 red taskmanager nodes still in GCP console (`flink-taskmanager-red-ffad`). Drained by GKE node version:

```bash
kubectl --context gcp-aso1prod5-2-green get nodes -l role=flink-taskmanager --no-headers \
  | grep "1.33.11" | awk '{print $1}' \
  | xargs -I{} kubectl --context gcp-aso1prod5-2-green drain {} \
      --ignore-daemonsets --delete-emptydir-data --force
```

Verified no flink workloads on red nodes. ✅

---

## Phase 12 — Cleanup

**Removed from `gke_config.hcl`:**
- `flink-jobmanager-red` node pool block
- `flink-taskmanager-red` node pool block
- Labels, taints, oauth_scopes for both red pools

**Terragrunt apply result:**
```
Plan: 0 to add, 0 to change, 4 to destroy
```
(2 node pools + 2 random_id resources destroyed) ✅

**PR commits:**
1. `feat(aso1prod5)`: Add flink-{jobmanager,taskmanager}-black node pools
2. `fix(aso1prod5)`: Bump init-red max_count 5→7 to unblock autoscaler scheduling
3. `chore(aso1prod5)`: Remove flink-{jobmanager,taskmanager}-red node pools

PR ready to merge when approved.

---

## Issues Encountered & Resolutions

| # | Issue | Resolution |
|---|---|---|
| 1 | Branch created from wrong base (feature branch, not main) | Cherry-picked flink commit onto new branch from `origin/main` |
| 2 | `mistcli flink aso1prod5 jobs count` fails | Used `mistcli flink operator la/app aso1prod5 green migration-status` |
| 3 | Monitor script `monitor-flink-migration.sh` not found | Used `mistcli migration-status` |
| 4 | Autoscaler pod Pending ~90min (`flink-la-8xlarge-1-backup-green`) | Bumped `init-red max_count` 5→7, committed, applied terragrunt |
| 5 | `zsh: read-only variable: status` in while loop | Renamed variable to `pstatus` |
| 6 | Red nodes still visible in console after cordon | Needed explicit `kubectl drain` to trigger GKE CA node deletion |

---

## Key Commands Reference

```bash
ENV=aso1prod5
CONTEXT=gcp-${ENV}-2-green

# Auth
export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token \
  --impersonate-service-account terraform-create-${ENV}@mist-infrastructure-iam.iam.gserviceaccount.com)

# Node pool status
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager --no-headers
kubectl --context $CONTEXT get nodes -l role=flink-jobmanager --no-headers

# Migration status
mistcli flink operator la ${ENV} green migration-status
mistcli flink operator app ${ENV} green migration-status

# Job counts (progress check)
mistcli flink operator la ${ENV} green migration-status | grep "Jobs running"
mistcli flink operator app ${ENV} green migration-status | grep "Jobs running"

# Cordon
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager -o name | xargs kubectl --context $CONTEXT cordon
kubectl --context $CONTEXT get nodes -l role=flink-jobmanager -o name | xargs kubectl --context $CONTEXT cordon

# Drain old nodes by version
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager --no-headers \
  | grep "1.33.11" | awk '{print $1}' \
  | xargs -I{} kubectl --context $CONTEXT drain {} --ignore-daemonsets --delete-emptydir-data --force
```
