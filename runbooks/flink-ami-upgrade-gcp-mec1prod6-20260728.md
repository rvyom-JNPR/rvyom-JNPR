# Flink GKE Upgrade Runbook — mec1prod6-gcp — 2026-07-28

## Summary

| Field | Value |
|---|---|
| Environment | `mec1prod6-gcp` (Middle East Cloud Production) |
| GKE Cluster | `mec1prod6-2-green` (europe-west3) |
| kubectl context | `gcp-mec1prod6-2-green` |
| IAC path | `mec1prod6-gcp/auto/gke-2-green/gke_config.hcl` |
| Old version | `1.33.11-gke.1074000` (red) |
| New version | `1.33.13-gke.1011000` (black, GKE auto-upgraded from 1.33.12) |
| PR | [mistsys/iac#4289](https://github.com/mistsys/iac/pull/4289) |
| Jira | [MIST-208595](https://mistsys.atlassian.net/browse/MIST-208595) |
| Date | 2026-07-28 |
| Operator | rvyom |

---

## Flink Clusters

| Namespace | URL | Type |
|---|---|---|
| flink-la-12xlarge-1-green | flink-la-12xlarge-1-green-mec1prod6.mist.pvt | la primary |
| flink-la-12xlarge-1-backup-green | flink-la-12xlarge-1-backup-green-mec1prod6.mist.pvt | la backup |
| flink-la-medium-1-green | flink-la-medium-1-green-mec1prod6.mist.pvt | la primary |
| flink-la-medium-1-backup-green | flink-la-medium-1-backup-green-mec1prod6.mist.pvt | la backup |
| flink-la-medium-2-green | flink-la-medium-2-green-mec1prod6.mist.pvt | la primary |
| flink-la-medium-2-backup-green | flink-la-medium-2-backup-green-mec1prod6.mist.pvt | la backup |
| flink-la-small-1-green | flink-la-small-1-green-mec1prod6.mist.pvt | la primary |
| flink-la-small-1-backup-green | flink-la-small-1-backup-green-mec1prod6.mist.pvt | la backup |
| flink-la-small-2-green | flink-la-small-2-green-mec1prod6.mist.pvt | la primary |
| flink-la-small-2-backup-green | flink-la-small-2-backup-green-mec1prod6.mist.pvt | la backup |
| flink-app-8xlarge-1-green | flink-app-8xlarge-1-green-mec1prod6.mist.pvt | app primary |
| flink-app-8xlarge-1-backup-green | flink-app-8xlarge-1-backup-green-mec1prod6.mist.pvt | app backup |

**Job counts at start:**
- LA: 736 jobs across 5 primary clusters
- App: 18 jobs across 1 primary cluster

---

## Phase 1 — Discovery

Active color: **red** (`flink-jobmanager-red`, `flink-taskmanager-red` @ `1.33.11-gke.1074000`)

Node counts at start:
- taskmanager-red: 16 nodes
- jobmanager-red: 6 nodes

```bash
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-taskmanager --no-headers
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-jobmanager --no-headers
```

---

## Phase 2 — Create Black Node Pools

**Branch**: `flink-nodepool/mec1prod6-black-20260728` (cut from `origin/main`)

Added to `gke_config.hcl`:
- `flink-jobmanager-black` — n2d-standard-8, max 20, version `1.33.12-gke.1270000` (actual: `1.33.13-gke.1011000`)
- `flink-taskmanager-black` — e2-highmem-8, max 48, version `1.33.12-gke.1270000` (actual: `1.33.13-gke.1011000`)

**PR**: [mistsys/iac#4289](https://github.com/mistsys/iac/pull/4289)

---

## Phase 2b — Terragrunt Apply

```
Plan: 4 to add, 0 to change, 0 to destroy
```
Resources created:
- `google_container_node_pool.pools["flink-jobmanager-black"]` → `flink-jobmanager-black-286c`
- `google_container_node_pool.pools["flink-taskmanager-black"]` → `flink-taskmanager-black-7055`
- `random_id.name["flink-jobmanager-black"]`
- `random_id.name["flink-taskmanager-black"]`

Applied successfully. Master version: `1.33.13-gke.1011000` (auto-upgraded by GKE from `1.33.12-gke.1270000`).

> ⚠️ **Note**: GKE auto-upgraded the new node pools to `1.33.13-gke.1011000` to match the current cluster master version. Updated `gke_config.hcl` to reflect actual version.

**GKE node pool suffixes after apply:**
- jobmanager-black: `286c`
- taskmanager-black: `7055`

---

## Phase 3 — Verify

```bash
kubectl --context gcp-mec1prod6-2-green get nodes -l node_pool=flink-jobmanager-black
kubectl --context gcp-mec1prod6-2-green get nodes -l node_pool=flink-taskmanager-black
```

New black pools visible in GKE. 0 nodes (min=0, inflate not yet applied). ✅

---

## Phase 4 — Cordon Old Red Nodes

```bash
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-taskmanager -o name | xargs kubectl --context gcp-mec1prod6-2-green cordon
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-jobmanager -o name | xargs kubectl --context gcp-mec1prod6-2-green cordon
```

Verify:
```bash
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-taskmanager --no-headers
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-jobmanager --no-headers
```
All red nodes should show: `Ready,SchedulingDisabled`

---

## Phase 5 — Inflate Pods

Inflate manifest location: `~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/`

Update replicas to match current red node counts before applying:
- `flink-jobmanager-black.yaml` — replicas: 6
- `flink-taskmanager-black.yaml` — replicas: 16

```bash
kubectl --context gcp-mec1prod6-2-green apply -f ~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/flink-jobmanager-black.yaml
kubectl --context gcp-mec1prod6-2-green apply -f ~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/flink-taskmanager-black.yaml
```

Monitor provisioning:
```bash
kubectl --context gcp-mec1prod6-2-green get pods -n default -l app=inflate-flink-taskmanager-black
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-taskmanager --no-headers | wc -l
```

---

## Phase 6 — Cluster URLs

Discovered via:
```bash
kubectl --context gcp-mec1prod6-2-green get ingress -A \
  -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.spec.rules[0].host}{"\n"}{end}' \
  | grep -E 'flink-la|flink-app' | sort
```

---

## Phase 7 — Migrate Jobs to Backup

```bash
mistcli flink operator la mec1prod6 green move-all-jobs   # RequestID: 4a819dda-85be-4f88-819a-eb6b655b45c7
mistcli flink operator app mec1prod6 green move-all-jobs  # RequestID: 00b7aaf6-7996-41eb-b956-82df8cd71037
```

---

## Phase 8 — Monitor

```bash
mistcli flink operator la mec1prod6 green migration-status
mistcli flink operator app mec1prod6 green migration-status
```

| Time | LA primary | LA backup | App primary | App backup |
|---|---|---|---|---|
| T+0 | 736 (100%) | 0 | 18 (100%) | 0 |
| T+30min | 582 (79%) | 149 (20%) | 0 | 18 (100%) |
| T+complete | 0 | 736 (100%) | 0 | 18 (100%) |

---

## Phase 9 — Restart Primary JobManagers

```bash
kubectl --context gcp-mec1prod6-2-green get pods -A -l component=jobmanager --no-headers \
  | grep -v backup | awk '{print $1, $2}' | while read ns pod; do
    node=$(kubectl --context gcp-mec1prod6-2-green get pod $pod -n $ns -o jsonpath='{.spec.nodeName}' 2>/dev/null)
    pool=$(kubectl --context gcp-mec1prod6-2-green get node $node -o jsonpath='{.metadata.labels.node_pool}' 2>/dev/null)
    if [[ "$pool" == "flink-jobmanager-red" ]]; then
      echo "Deleting $pod in $ns (on red node $node)"
      kubectl --context gcp-mec1prod6-2-green delete pod $pod -n $ns
    fi
  done
```

> ⚠️ Do NOT use `status` as a variable in zsh — it is read-only. Use `pstatus` or another name.

---

## Phase 10 — Move Jobs Back to Primary

```bash
mistcli flink operator la mec1prod6 green move-all-jobs-back   # RequestID: 801297f5-3921-4d6b-a2c9-b2ed06dd9fa1
mistcli flink operator app mec1prod6 green move-all-jobs-back  # RequestID: d8c29e3d-d429-4b8a-abe1-f00dc8d4ead0
```

Final state:
- LA: 736/736 on primary (100%) ✅
- App: 18/18 on primary (100%) ✅

---

## Phase 11 — Drain Red Nodes

```bash
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-taskmanager --no-headers \
  | grep "1.33.11" | awk '{print $1}' \
  | xargs -I{} kubectl --context gcp-mec1prod6-2-green drain {} \
      --ignore-daemonsets --delete-emptydir-data --force

kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-jobmanager --no-headers \
  | grep "1.33.11" | awk '{print $1}' \
  | xargs -I{} kubectl --context gcp-mec1prod6-2-green drain {} \
      --ignore-daemonsets --delete-emptydir-data --force
```

Also scale down inflate replicas to 0:
```bash
kubectl --context gcp-mec1prod6-2-green scale deployment inflate-flink-taskmanager-black -n default --replicas=0
kubectl --context gcp-mec1prod6-2-green scale deployment inflate-flink-jobmanager-black -n default --replicas=0
```

---

## Phase 12 — Cleanup

**Remove from `gke_config.hcl`:**
- `flink-jobmanager-red` node pool block
- `flink-taskmanager-red` node pool block

**Terragrunt apply:**
```bash
export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token \
  --impersonate-service-account terraform-create-mec1prod6@mist-infrastructure-iam.iam.gserviceaccount.com)
cd ~/workspace/iac/mec1prod6-gcp/auto/gke-2-green
terragrunt plan   # Expected: 0 to add, 0 to change, 4 to destroy
terragrunt apply
```

> ✅ **Verify red nodes are at 0 before applying**: Check the GCP console node list to confirm `flink-jobmanager-red` and `flink-taskmanager-red` pools show 0 nodes before running terragrunt destroy.
> Console: https://console.cloud.google.com/kubernetes/clusters/details/europe-west3/mec1prod6-2-green/nodes?project=mist-k8s-mec1prod6
>
> Confirmed 2026-07-28: both `flink-jobmanager-red` and `flink-taskmanager-red` showed **0 nodes** before cleanup apply. ✅

**PR commits:**
1. `feat(mec1prod6)`: Add flink-{jobmanager,taskmanager}-black node pools
2. `fix(mec1prod6)`: Sync black node pool version to `1.33.13-gke.1011000`
3. `chore(mec1prod6)`: Remove flink-{jobmanager,taskmanager}-red node pools

PR ready to merge when approved.

---

## Issues Encountered & Resolutions

| # | Issue | Resolution |
|---|---|---|
| 1 | GKE auto-upgraded black pools to `1.33.13-gke.1011000` instead of `1.33.12-gke.1270000` | Expected GKE behavior — cluster master was already at `1.33.13`. Updated `gke_config.hcl` to reflect actual version. |
| 2 | Red taskmanager nodes disappeared before explicit drain | GKE CA automatically deleted cordoned+empty TM nodes. No manual drain needed for TM. |
| 3 | `terragrunt plan` opened a pager in terminal | Used file redirection to capture output; applied manually in separate terminal. |

---

## Key Commands Reference

```bash
ENV=mec1prod6
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

# Cordon
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager -o name | xargs kubectl --context $CONTEXT cordon
kubectl --context $CONTEXT get nodes -l role=flink-jobmanager -o name | xargs kubectl --context $CONTEXT cordon

# Drain old nodes by version
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager --no-headers \
  | grep "1.33.11" | awk '{print $1}' \
  | xargs -I{} kubectl --context $CONTEXT drain {} --ignore-daemonsets --delete-emptydir-data --force
```
