# Flink AMI Upgrade — AWS EU Environment
**Date**: 2026-07-22
**Environment**: AWS EU (`eu`), cluster `eu-2-green`, region `eu-central-1`
**New AMI**: `ami-05f1f191774d455f7` (`eu-central-1-mist-eks-1.33-ubuntu-24.04-amd64-20260623-080957`)
**Profile**: `eu-admin`
**JIRA**: MIST-205129 | **PR**: mistsys/mist-sk8r#56543

---

## Background — Red-Black Deployment Strategy

Flink nodes run on Karpenter NodePools named either `flink-taskmanager-red` or `flink-taskmanager-black` (same for jobmanager). When upgrading the AMI, instead of upgrading nodes in place (which would kill running jobs), we:
1. Create a second set of node pools with the new AMI (the opposite color)
2. Migrate all work to those new nodes
3. Drain and retire the old nodes

This gives **zero downtime**.

---

## Steps

### Step 1 — Authenticate and Connect to Cluster

```bash
cd ~/workspace/mist-sk8r/eu-2-green.eks
PROFILE=admin bash init-kube-config
```

Ran `aws eks update-kubeconfig` to add the `eu-2-green` context. All subsequent `kubectl` commands needed `aws-okta exec eu-admin --` prefix to inject temporary AWS credentials.

---

### Step 2 — Verify New Node Pools Exist (Phase 3)

PR #56543 had already been merged, adding `flink-taskmanager-black` and `flink-jobmanager-black` NodePool and EC2NodeClass resources via Flux GitOps.

Confirmed NodePools were `Ready=True` with 0 nodes:

```
flink-jobmanager-black    0 nodes   Ready
flink-jobmanager-red     11 nodes   Ready  ← old, active
flink-taskmanager-black   0 nodes   Ready
flink-taskmanager-red   123 nodes   Ready  ← old, active
```

0 nodes on black is expected — Karpenter only provisions nodes when pods need to be scheduled.

---

### Step 3 — Cordon All Old (Red) Nodes (Phase 4)

Cordoning marks nodes as `SchedulingDisabled` — Kubernetes will not place any new pods on them, but existing pods keep running undisturbed.

```bash
aws-okta exec eu-admin -- bash -c '
  kubectl --context=eu-2-green get nodes \
    -l karpenter.sh/nodepool=flink-taskmanager-red -o name \
    | xargs kubectl --context=eu-2-green cordon
'
# Repeated for flink-jobmanager-red
```

> ⚠️ Must wrap in `bash -c` — `xargs kubectl` inside a subshell loses `aws-okta` credentials otherwise.

Result: All 122 taskmanager-red + 11 jobmanager-red nodes cordoned.

---

### Step 4 — Provision New Black Nodes via Inflate Pods (Phase 5)

Karpenter won't provision nodes unless pods are pending. We used "inflate" deployments — dummy `pause` containers with `podAntiAffinity` (one pod per node) to force Karpenter to create exactly as many nodes as needed.

**Target**: 127 taskmanager-black (122 + 5 buffer) and 16 jobmanager-black (11 + 5 buffer).

Applied manifests from `~/workspace/devops/adhoc/k8s/inflate-karpenter-nodepools/eks-2-black/` then scaled up in ~30% increments:

```
Taskmanager: 20 → 50 → 90 → 127
Jobmanager:  10 →  6 → 12 →  16
```

All 143 nodes reached `Ready` status.

---

### Step 5 — Record Baseline Job Count (Phase 6)

```bash
aws-okta exec eu-admin -- mistcli flink eu jobs count
```

**Result**: 688 jobs running across 10 primary `flink-la-*` clusters. All backup clusters returned HTTP 503 (standby — expected).

---

### Step 6 — Trigger Job Migration to Backup Clusters (Phase 7)

The Flink operator checkpoints all jobs on primary clusters and restarts them on backup clusters.

```bash
aws-okta exec eu-admin -- mistcli flink operator la eu green move-all-jobs
# Prompted: "Are you sure?" → yes
```

> ⚠️ `--watch` flag does NOT exist on this command. Monitor separately (see Step 7).

Response: `HTTP 202 — Command scheduled successfully`

---

### Step 7 — Monitor Migration Progress (Phase 8)

```bash
aws-okta exec eu-admin -- mistcli flink operator la eu green migration-status --watch
```

Refreshed every 5 seconds showing per-cluster job counts. Waited ~1 hour until:

```
Status: COMPLETE (on backup)
Jobs running on primary:  0 (0%)
Jobs running on backup:  736 (100%)
Jobs failed: 0
```

---

### Step 8 — Restart Primary Cluster JobManagers (Phase 9)

With jobs safely on backup clusters, restarted all jobmanager deployments in the 10 primary namespaces. Since red nodes are cordoned, new pods scheduled onto black nodes.

> ⚠️ `kubectl rollout restart -l <label>` is NOT supported — must get deployment names first.
> ⚠️ Only restart PRIMARY namespaces (not backup).

```bash
aws-okta exec eu-admin -- bash -c '
for ns in flink-la-10xlarge-1-green flink-la-10xlarge-2-green flink-la-11xlarge-1-green \
          flink-la-12xlarge-1-green flink-la-12xlarge-2-green flink-la-12xlarge-3-green \
          flink-la-12xlarge-4-green flink-la-12xlarge-5-green flink-la-8xlarge-1-green \
          flink-la-8xlarge-2-green; do
  deploys=$(kubectl --context=eu-2-green get deployment -n $ns \
    -l component=jobmanager -o name)
  echo "$deploys" | xargs kubectl --context=eu-2-green rollout restart -n $ns && echo "✓ $ns"
done
'
```

> ⚠️ **Edge case**: Karpenter may provision new uncordoned red nodes before pods land on black. If jobmanagers land on new red nodes, cordon and drain those nodes:
> ```bash
> aws-okta exec eu-admin -- kubectl --context=eu-2-green \
>   get nodes -l karpenter.sh/nodepool=flink-jobmanager-red --no-headers \
>   | grep -v SchedulingDisabled
> # If any appear, drain them to force pods onto black
> ```

All 11 primary jobmanagers reached `Running` on black nodes.

---

### Step 9 — Move Jobs Back to Primary Clusters (Phase 10)

```bash
aws-okta exec eu-admin -- mistcli flink operator la eu green move-all-jobs-back
# Prompted: yes

# Monitor:
aws-okta exec eu-admin -- mistcli flink operator la eu green migration-status --watch
```

Waited ~1 hour until:

```
Status: COMPLETE (on primary)
Jobs running on primary:  736 (100%)
Jobs running on backup:     0
Jobs failed: 0
```

---

### Step 10 — Cleanup and Finalize (Phases 11 & 12)

**Delete inflate deployments:**
```bash
aws-okta exec eu-admin -- kubectl --context=eu-2-green delete deployment \
  inflate-flink-taskmanager-black inflate-flink-jobmanager-black -n default
```

**Verify final job count:**
```bash
aws-okta exec eu-admin -- mistcli flink eu jobs count
# Result: 736 total, 734 running (2 still warming up — normal)
```

**Old red nodes**: Auto-terminated by Karpenter's consolidation policy (`WhenEmpty`, 15-minute delay). No manual deletion needed.

**PR comment**: Added completion note to mistsys/mist-sk8r#56543.

---

## Final State

| Metric | Value |
|--------|-------|
| Jobs before | 688 running |
| Jobs after | 736 running (0 failed, 0 cancelled) |
| Old nodes retired | 122 TM-red + 11 JM-red → 0 |
| New nodes active | 229 TM-black + 16 JM-black |
| Total downtime | **Zero** |
| Migration duration | ~2.5 hours |
| AMI deployed | `ami-05f1f191774d455f7` (EKS 1.33, Ubuntu 24.04, 2026-06-23) |

---

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| `xargs kubectl` — Unauthorized | Wrap in `aws-okta exec <env>-admin -- bash -c '...'` |
| `kubectl rollout restart -l` — unknown flag | Get deployment names first, then pipe to restart |
| `move-all-jobs --watch` — unknown flag | Remove `--watch`; monitor with `migration-status --watch` |
| Only 2/10 namespaces restart in loop | Wrap loop in `aws-okta exec ... -- bash -c '...'` |
| Jobmanagers land on new red nodes | Karpenter provisions new red nodes — cordon and drain them |
| Monitor script not found | Use `mistcli flink operator la eu green migration-status --watch` |