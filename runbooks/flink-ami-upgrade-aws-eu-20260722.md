# Flink AMI Upgrade — AWS EU Environment

**Date**: 2026-07-22
**Environment**: AWS EU (`eu`), cluster `eu-2-green`, region `eu-central-1`
**New AMI**: `ami-05f1f191774d455f7` (`eu-central-1-mist-eks-1.33-ubuntu-24.04-amd64-20260623-080957`)
**Profile**: `eu-admin`
**JIRA**: MIST-205129 | **PR**: mistsys/mist-sk8r#56543

## Background

Flink nodes run on Karpenter NodePools named `flink-taskmanager-red/black`. To upgrade AMI with zero downtime:
1. Create new node pools with new AMI (opposite color)
2. Migrate all jobs to backup clusters
3. Restart jobmanagers on new nodes
4. Move jobs back to primary clusters
5. Drain old nodes

## Steps

### Step 1 — Connect to Cluster
```bash
cd ~/workspace/mist-sk8r/eu-2-green.eks && PROFILE=admin bash init-kube-config
```

### Step 2 — Verify New Node Pools (Phase 3)
PR #56543 merged, adding black NodePools via Flux. Verified Ready=True with 0 nodes (expected).
```bash
aws-okta exec eu-admin -- kubectl --context=eu-2-green get nodepools | grep flink
```

### Step 3 — Cordon Old Red Nodes (Phase 4)
```bash
aws-okta exec eu-admin -- bash -c '
  kubectl --context=eu-2-green get nodes \
    -l karpenter.sh/nodepool=flink-taskmanager-red -o name \
    | xargs kubectl --context=eu-2-green cordon
'
# Repeated for flink-jobmanager-red
```
Must wrap in `bash -c` — `xargs` inside subshell loses aws-okta credentials.

Result: 122 TM-red + 11 JM-red nodes cordoned.

### Step 4 — Provision Black Nodes via Inflate Pods (Phase 5)
Applied dummy pause deployments with podAntiAffinity (1 pod/node) to force Karpenter provisioning.
Target: 127 TM-black (122+5 buffer), 16 JM-black (11+5 buffer). Scaled in 30% increments:
- TM: 20 -> 50 -> 90 -> 127
- JM: 10 -> 6 -> 12 -> 16

All 143 nodes reached Ready.

### Step 5 — Baseline Job Count (Phase 6)
```bash
aws-okta exec eu-admin -- mistcli flink eu jobs count
# Result: 688 jobs running across 10 primary flink-la-* clusters
```

### Step 6 — Move Jobs to Backup Clusters (Phase 7)
```bash
aws-okta exec eu-admin -- mistcli flink operator la eu green move-all-jobs
# Prompted: yes
```
Note: --watch flag does NOT exist on this command. Monitor separately.

### Step 7 — Monitor Migration (Phase 8)
```bash
aws-okta exec eu-admin -- mistcli flink operator la eu green migration-status --watch
```
Waited ~1 hour. Result: COMPLETE (on backup) - 736 jobs on backup, 0 failed.

### Step 8 — Restart Primary JobManagers (Phase 9)
```bash
aws-okta exec eu-admin -- bash -c '
for ns in flink-la-10xlarge-1-green flink-la-10xlarge-2-green flink-la-11xlarge-1-green \
          flink-la-12xlarge-1-green flink-la-12xlarge-2-green flink-la-12xlarge-3-green \
          flink-la-12xlarge-4-green flink-la-12xlarge-5-green \
          flink-la-8xlarge-1-green flink-la-8xlarge-2-green; do
  deploys=$(kubectl --context=eu-2-green get deployment -n $ns \
    -l component=jobmanager -o name)
  echo "$deploys" | xargs kubectl --context=eu-2-green rollout restart -n $ns
done
'
```
Notes:
- kubectl rollout restart -l is NOT supported. Get deployment names first.
- Only restart PRIMARY namespaces (not backup).
- Edge case: Karpenter may provision new uncordoned red nodes. Check and drain:
  `kubectl get nodes -l karpenter.sh/nodepool=flink-jobmanager-red | grep -v SchedulingDisabled`

### Step 9 — Move Jobs Back to Primary (Phase 10)
```bash
aws-okta exec eu-admin -- mistcli flink operator la eu green move-all-jobs-back
aws-okta exec eu-admin -- mistcli flink operator la eu green migration-status --watch
```
Result: COMPLETE (on primary) - 736 jobs running, 0 failed.

### Step 10 — Cleanup (Phases 11-12)
```bash
aws-okta exec eu-admin -- kubectl --context=eu-2-green delete deployment \
  inflate-flink-taskmanager-black inflate-flink-jobmanager-black -n default
aws-okta exec eu-admin -- mistcli flink eu jobs count
```
Old red nodes auto-terminated by Karpenter consolidation (WhenEmpty, 15min delay).

## Final State

| Metric | Value |
|--------|-------|
| Jobs before | 688 running |
| Jobs after | 736 running (0 failed) |
| Old nodes retired | 122 TM-red + 11 JM-red -> 0 |
| New nodes active | 229 TM-black + 16 JM-black |
| Total downtime | Zero |
| Duration | ~2.5 hours |
| AMI | ami-05f1f191774d455f7 (EKS 1.33, Ubuntu 24.04, 2026-06-23) |

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| xargs kubectl Unauthorized | Wrap in `aws-okta exec <env>-admin -- bash -c '...'` |
| rollout restart -l unknown flag | Get deployment names first, pipe to restart |
| move-all-jobs --watch unknown flag | Remove --watch, use migration-status --watch separately |
| Only 2/10 namespaces restart | Wrap loop in `aws-okta exec ... -- bash -c '...'` |
| Jobmanagers land on new red nodes | Karpenter re-provisions red nodes - drain them |
| Monitor script not found | Use migration-status --watch instead |
