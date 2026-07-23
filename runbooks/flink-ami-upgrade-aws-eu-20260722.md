# Flink AMI Upgrade — AWS EU Environment

**Date**: 2026-07-22
**Environment**: AWS EU (`eu`), cluster `eu-2-green`, region `eu-central-1`
**New AMI**: `ami-05f1f191774d455f7` (`eu-central-1-mist-eks-1.33-ubuntu-24.04-amd64-20260623-080957`)
**JIRA**: MIST-205129 | **PR**: mistsys/mist-sk8r#56543

---

## Session Setup

Always resolve the admin profile automatically from `~/.aws/config`:

```bash
ENV=eu
PROFILE=$(grep -o "profile ${ENV}-[a-z]*" ~/.aws/config | grep "\-admin$" | head -1 | awk '{print $2}')
echo "Using profile: $PROFILE"   # eu-admin
CONTEXT="${ENV}-2-green"

# Init kubectl context
cd ~/workspace/mist-sk8r/${CONTEXT}.eks
PROFILE_NAME=$(echo $PROFILE | sed "s/${ENV}-//") PROFILE=$PROFILE_NAME bash init-kube-config
```

---

## Background

Flink nodes run on Karpenter NodePools named `flink-taskmanager-red/black`. To upgrade AMI with zero downtime:
1. Create new node pools with new AMI (opposite color)
2. Cordon old nodes
3. Provision new nodes via inflate pods
4. Migrate all jobs to backup clusters
5. Restart jobmanagers on new nodes
6. Move jobs back to primary clusters
7. Old nodes auto-terminate via Karpenter consolidation

---

## Step 1 — Verify New Node Pools (Phase 3)
PR #56543 merged, adding `flink-taskmanager-black` and `flink-jobmanager-black` NodePools via Flux.
```bash
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT get nodepools | grep flink
# flink-taskmanager-black  0 nodes  Ready  <-- new, 0 nodes expected
# flink-taskmanager-red  123 nodes  Ready  <-- old, active
```

## Step 2 — Cordon Old Red Nodes (Phase 4)
```bash
aws-okta exec $PROFILE -- bash -c '
  kubectl --context=$CONTEXT get nodes \
    -l karpenter.sh/nodepool=flink-taskmanager-red -o name \
    | xargs kubectl --context=$CONTEXT cordon
  kubectl --context=$CONTEXT get nodes \
    -l karpenter.sh/nodepool=flink-jobmanager-red -o name \
    | xargs kubectl --context=$CONTEXT cordon
'
```
> Must wrap in `bash -c` — `xargs kubectl` inside subshell loses aws-okta credentials.

Result: 122 TM-red + 11 JM-red nodes cordoned.

## Step 3 — Provision Black Nodes via Inflate Pods (Phase 5)
Target = current node count + 5 buffer.
```bash
# Apply manifests
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT apply \
  -f ~/workspace/devops/adhoc/k8s/inflate-karpenter-nodepools/eks-2-black/flink-taskmanager-black.yaml
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT apply \
  -f ~/workspace/devops/adhoc/k8s/inflate-karpenter-nodepools/eks-2-black/flink-jobmanager-black.yaml

# Scale in ~30% increments (example: 122 TM -> 127 target, 11 JM -> 16 target)
# TM: 20 -> 50 -> 90 -> 127
# JM: 10 -> 6 -> 12 -> 16
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT scale deployment inflate-flink-taskmanager-black --replicas=127
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT scale deployment inflate-flink-jobmanager-black --replicas=16
```

## Step 4 — Baseline Job Count (Phase 6)
```bash
aws-okta exec $PROFILE -- mistcli flink $ENV jobs count
# Result: 688 jobs running across 10 primary flink-la-* clusters
```

## Step 5 — Move Jobs to Backup Clusters (Phase 7)
```bash
aws-okta exec $PROFILE -- mistcli flink operator la $ENV green move-all-jobs
# Prompted: yes
# Note: --watch flag does NOT exist. Monitor separately.
```

## Step 6 — Monitor Migration (Phase 8)
```bash
aws-okta exec $PROFILE -- mistcli flink operator la $ENV green migration-status --watch
# Wait for: Status: COMPLETE (on backup), Jobs failed: 0
```

## Step 7 — Restart Primary JobManagers (Phase 9)
```bash
# Get primary namespaces dynamically (exclude backup)
NAMESPACES=$(aws-okta exec $PROFILE -- kubectl --context=$CONTEXT get ns -o name \
  | grep flink-la | grep -v backup | sed 's|namespace/||')

aws-okta exec $PROFILE -- bash -c "
for ns in $NAMESPACES; do
  deploys=\$(kubectl --context=$CONTEXT get deployment -n \$ns \
    -l component=jobmanager -o name)
  [ -n "\$deploys" ] && echo "\$deploys" \
    | xargs kubectl --context=$CONTEXT rollout restart -n \$ns && echo v \$ns
done
"
```
> `kubectl rollout restart -l` is NOT supported — get deployment names first.
> Only restart PRIMARY namespaces (not backup).
> **Edge case**: Check for new uncordoned red nodes — Karpenter may provision them:
> ```bash
> aws-okta exec $PROFILE -- kubectl --context=$CONTEXT \
>   get nodes -l karpenter.sh/nodepool=flink-jobmanager-red --no-headers \
>   | grep -v SchedulingDisabled  # drain any that appear
> ```

## Step 8 — Move Jobs Back to Primary (Phase 10)
```bash
aws-okta exec $PROFILE -- mistcli flink operator la $ENV green move-all-jobs-back
# Prompted: yes
aws-okta exec $PROFILE -- mistcli flink operator la $ENV green migration-status --watch
# Wait for: Status: COMPLETE (on primary), 736 jobs running, 0 failed
```

## Step 9 — Cleanup (Phases 11-12)
```bash
# Delete inflate deployments
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT delete deployment \
  inflate-flink-taskmanager-black inflate-flink-jobmanager-black -n default

# Verify final count
aws-okta exec $PROFILE -- mistcli flink $ENV jobs count

# Add completion comment to PR
cd ~/workspace/mist-sk8r
gh pr comment <PR_NUMBER> --body "## AMI Upgrade Complete
Jobs: 736 running, 0 failed. Old red nodes drained to 0."
```
Old red nodes auto-terminated by Karpenter consolidation (WhenEmpty, 15min delay).

---

## Final State

| Metric | Value |
|--------|-------|
| Jobs before | 688 running |
| Jobs after | 736 running (0 failed) |
| Old nodes retired | 122 TM-red + 11 JM-red -> 0 |
| New nodes active | 229 TM-black + 16 JM-black |
| Total downtime | **Zero** |
| Duration | ~2.5 hours |
| AMI | `ami-05f1f191774d455f7` (EKS 1.33, Ubuntu 24.04, 2026-06-23) |

---

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| `xargs kubectl` Unauthorized | Wrap in `aws-okta exec $PROFILE -- bash -c '...'` |
| `rollout restart -l` unknown flag | Get deployment names first, pipe to restart |
| `move-all-jobs --watch` unknown flag | Remove --watch, use `migration-status --watch` separately |
| Only 2/10 namespaces restart in loop | Wrap loop in `aws-okta exec $PROFILE -- bash -c '...'` |
| Jobmanagers land on new red nodes | Karpenter re-provisions red nodes — cordon and drain them |
| Monitor script not found | Use `migration-status --watch` instead |
| Wrong AWS profile | Run `grep -o "profile ${ENV}-[a-z]*" ~/.aws/config` to find correct one |
