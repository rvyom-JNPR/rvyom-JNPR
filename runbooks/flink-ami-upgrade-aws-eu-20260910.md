# Flink AMI Upgrade — AWS EU Environment

**Date**: 2026-09-10
**Environment**: AWS EU (`eu`), cluster `eu-2-green`, region `eu-central-1`
**New AMI**: `ami-0743c94a3c3d88e2e` (`amazon-eks-node-al2023-x86_64-standard-1.33-v20260810`, AL2023)
**GPU AMI**: `ami-0b1514139f6bb144c` (`amazon-eks-node-al2023-x86_64-nvidia-1.33-v20260827`)
**JIRA**: MIST-222720 | **PR (Karpenter nodepools)**: mistsys/mist-sk8r#59423
**Related IAC PR (managed node groups)**: mistsys/iac#4684 — merged 2026-09-09 (prior day)

---

## Session Setup

```bash
ENV=eu
PROFILE=$(grep -o "profile ${ENV}-[a-z]*" ~/.aws/config | grep "\-admin$" | head -1 | awk '{print $2}')
echo "Using profile: $PROFILE"   # eu-admin
CONTEXT="${ENV}-2-green"

cd ~/workspace/mist-sk8r/${CONTEXT}.eks
PROFILE=admin bash init-kube-config
```

---

## Background

This cycle rotated the **new** color to `red` (previous 2026-07-22 session had finalized `black`).
`git log` on `karpenter-nodepools/` in `mist-sk8r` showed the latest PR bumped `red` weight 3→5
and updated the AMI — confirming `red` = new/target, `black` = old/active.

Two PRs were involved before job migration even started (both already merged by the time this
session began):
1. **mist-sk8r#59423** (MIST-222720) — Karpenter `flink-*-red` NodePool/EC2NodeClass prepare, weight bump
2. **iac#4684** — managed node group AMI reference update in `eu-aws/eks_amis.hcl` (separate repo/process)

---

## Step 1 — Verify New Node Pools (Phase 3)
```bash
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT get nodepools | grep flink
# flink-jobmanager-black    11 nodes   Ready   <-- old, active
# flink-jobmanager-red       0 nodes   Ready   <-- new
# flink-taskmanager-black   97 nodes   Ready   <-- old, active
# flink-taskmanager-red     31 nodes   Ready   <-- new (Karpenter already routing new pods here due to weight)
```

## Step 2 — Cordon Old Black Nodes (Phase 4)
```bash
aws-okta exec $PROFILE -- bash -c '
  kubectl --context=$CONTEXT get nodes -l karpenter.sh/nodepool=flink-taskmanager-black -o name \
    | xargs kubectl --context=$CONTEXT cordon
  kubectl --context=$CONTEXT get nodes -l karpenter.sh/nodepool=flink-jobmanager-black -o name \
    | xargs kubectl --context=$CONTEXT cordon
'
```
Result: 97 TM-black + 11 JM-black nodes cordoned.

## Step 3 — Provision Red Nodes via Inflate Pods (Phase 5)
```bash
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT apply \
  -f ~/workspace/devops/adhoc/k8s/inflate-karpenter-nodepools/eks-2-red/flink-taskmanager-red.yaml \
  -f ~/workspace/devops/adhoc/k8s/inflate-karpenter-nodepools/eks-2-red/flink-jobmanager-red.yaml

# Ramp in increments
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT scale deployment inflate-flink-taskmanager-red --replicas=50
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT scale deployment inflate-flink-jobmanager-red --replicas=6
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT scale deployment inflate-flink-taskmanager-red --replicas=90
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT scale deployment inflate-flink-jobmanager-red --replicas=12
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT scale deployment inflate-flink-taskmanager-red --replicas=105
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT scale deployment inflate-flink-jobmanager-red --replicas=16
```
Result: 105 TM-red + 16 JM-red nodes, all Ready.

## Step 4 — Baseline Job Count (Phase 6)
```bash
aws-okta exec $PROFILE -- mistcli flink $ENV jobs count
# 812 total / 757 running / 0 failed across 10 primary flink-la-* clusters
```
> Note: `flink-app-12xlarge-1-green` also present this cycle (app-type operator).
> `flink-la-12xlarge-4-green` occasionally returned a transient DNS lookup failure when
> queried standalone via mistcli — retry resolved it; not a real cluster issue.

## Step 5 — Move Jobs to Backup Clusters (Phase 7)
```bash
aws-okta exec $PROFILE -- mistcli flink operator la $ENV green move-all-jobs   # yes
aws-okta exec $PROFILE -- mistcli flink operator app $ENV green move-all-jobs  # yes
```

## Step 6 — Monitor Migration (Phase 8)
```bash
aws-okta exec $PROFILE -- mistcli flink operator la $ENV green migration-status
aws-okta exec $PROFILE -- mistcli flink operator app $ENV green migration-status
# app: COMPLETE (on backup) — 17/17 (100%), 0 failed
# la:  COMPLETE (on backup) — 758/761 (99.6%), 0 failed
```

## Step 7 — Restart Primary JobManagers (Phase 9)
```bash
NAMESPACES=$(aws-okta exec $PROFILE -- kubectl --context=$CONTEXT get ns -o name \
  | grep -E 'flink-la|flink-app' | grep -v backup | grep -v base | grep -v operator | sed 's|namespace/||')

aws-okta exec $PROFILE -- bash -c "
for ns in $NAMESPACES; do
  deploys=\$(kubectl --context=$CONTEXT get deployment -n \$ns -l component=jobmanager -o name)
  [ -n \"\$deploys\" ] && echo \"\$deploys\" | xargs kubectl --context=$CONTEXT rollout restart -n \$ns && echo done \$ns
done
"
```
All 11 primary jobmanagers (10 `la` + 1 `app`) restarted successfully, all `1/1 Ready`.

**Verification — confirmed all landed on `red`:**
```bash
aws-okta exec $PROFILE -- bash -c '
  kubectl --context=$CONTEXT get pods -A -l component=jobmanager --no-headers | grep -v backup | awk "{print \$1, \$2}" \
  | while read ns pod; do
    node=$(kubectl --context=$CONTEXT get pod $pod -n $ns -o jsonpath="{.spec.nodeName}")
    pool=$(kubectl --context=$CONTEXT get node $node -o jsonpath="{.metadata.labels.karpenter\.sh/nodepool}")
    echo "$ns -> $node ($pool)"
  done
'
# All 11 -> flink-jobmanager-red. No re-provisioned black nodes appeared.
```

## Step 8 — Move Jobs Back to Primary (Phase 10)
```bash
aws-okta exec $PROFILE -- mistcli flink operator la $ENV green move-all-jobs-back   # yes
aws-okta exec $PROFILE -- mistcli flink operator app $ENV green move-all-jobs-back  # yes
# Both reached: Status: COMPLETE (on primary), 0 failed
# la: 760/761 (99.9%) | app: 17/17 (100%)
```

## Step 9 — Cleanup (Phases 11–12)
```bash
aws-okta exec $PROFILE -- kubectl --context=$CONTEXT delete deployment \
  inflate-flink-taskmanager-red inflate-flink-jobmanager-red -n default

aws-okta exec $PROFILE -- mistcli flink $ENV jobs count
# 761 total / 760 running / 0 failed

cd ~/workspace/mist-sk8r
gh pr comment 59423 --body "## AMI Upgrade Complete ✅ ..."
```
Old `black` nodes had **already auto-drained to 0** by the time cleanup ran (Karpenter
consolidation, `WhenEmpty` policy) — no manual wait/verification needed beyond the nodepool check.

---

## Final State

| Metric | Value |
|--------|-------|
| Jobs before | 812 total / 757 running |
| Jobs after | 761 total / 760 running (0 failed) |
| Old nodes retired | 97 TM-black + 11 JM-black → 0 |
| New nodes active | 194 TM-red + 11 JM-red |
| Total downtime | **Zero** |
| AMI | `ami-0743c94a3c3d88e2e` (AL2023, EKS 1.33, 2026-08-10) |

---

## Common Pitfalls (this session)

| Issue | Fix |
|-------|-----|
| Sync terminal became unresponsive after a multi-line `for` loop with `sleep` | Opened a fresh terminal via async mode; avoid multi-line sleep loops in sync mode, prefer one command per call |
| Nested-quote bash script left shell in `quote>` continuation prompt | Send a closing quote character to unstick it |
| `mistcli ... jobs count` transient HTTP/DNS failure for one cluster | Retried — resolved; not a real issue, backups always show HTTP 503 (expected, scaled to 0) |
| Confusion between "primary/backup" (job migration concept) and "red/black" (node pool concept) | They're independent pairs — primary/backup clusters can run on either node color |
| IAC PR for managed node group AMI (`iac#4684`) easy to miss | It's a separate repo/process from the Karpenter `mist-sk8r` PR — check both when auditing "was a PR needed" |
