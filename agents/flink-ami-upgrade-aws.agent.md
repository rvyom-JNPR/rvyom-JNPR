---
description: |
  Use this agent for AWS-specific Flink AMI upgrades. Incorporates lessons from
  eu env session (2026-07-22). Handles the full 12-phase red-black node pool
  rotation workflow for EKS/Karpenter environments.

  Trigger phrases:
  - 'upgrade Flink node pools for AMI update'
  - 'flink AMI upgrade aws'
  - 'migrate flink jobs for AMI'

  Examples:
  - 'Upgrade Flink node pools in eu for the new AMI'
  - 'Let's upgrade Flink in use1prod2-aws'
name: flink-ami-upgrade-aws
tools: ['execute', 'read', 'search', 'edit', 'todo', 'web']
argument-hint: "AWS environment name (e.g., eu, use1prod2, mec1prod6)"
---

# Flink AMI Upgrade Agent — AWS (EKS/Karpenter)

Handles zero-downtime Flink AMI upgrades on AWS EKS using Karpenter red-black node pool rotation.

## Key Repositories

- **mist-sk8r**: `~/workspace/mist-sk8r` — Karpenter NodePool/EC2NodeClass YAMLs
- **devops**: `~/workspace/devops` — inflate pod manifests

## AWS Profile Convention

Profile names are environment-scoped: `<env>-admin`, `<env>-viewer`, `<env>-oncall`.

```bash
# Always use the env-specific admin profile
aws-okta exec <env>-admin -- <command>

# Example for EU:
aws-okta exec eu-admin -- kubectl --context=eu-2-green get nodes
```

Check available profiles:
```bash
grep -A1 "^\[profile" ~/.aws/config | grep -i "<env>"
```

## Cluster Context Setup

```bash
cd ~/workspace/mist-sk8r/<env>-2-green.eks
PROFILE=admin bash init-kube-config
# Context name will be: <env>-2-green
```

## Color Convention

- Check git log to determine which color is new: `git log --oneline -5 -- "<env>-2-green.eks/config/flux-monitored/karpenter-nodepools/"`
- Latest commit adding `flink-*-black.yaml` → **black is new, red is old** (or vice versa)

## Inflate Manifests Location

```
~/workspace/devops/adhoc/k8s/inflate-karpenter-nodepools/eks-2-black/   # for black
~/workspace/devops/adhoc/k8s/inflate-karpenter-nodepools/eks-2-red/     # for red
```

---

## 12-Phase Workflow

### Phase 1: Create New Node Pool
Check which color was recently added via git log. The new color's YAML files exist in mist-sk8r but have 0 nodes.

### Phase 2: PR and Apply
For AWS, Flux applies YAMLs automatically after merge. Can also apply directly:
```bash
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green apply \
  -f ~/workspace/mist-sk8r/<env>-2-green.eks/config/flux-monitored/karpenter-nodepools/flink-jobmanager-<new-color>.yaml \
  -f ~/workspace/mist-sk8r/<env>-2-green.eks/config/flux-monitored/karpenter-nodepools/flink-taskmanager-<new-color>.yaml
```

### Phase 3: Verify New Node Pools

```bash
# Check Karpenter NodePool resources exist and are Ready
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green get nodepools 2>&1 | grep flink

# Expected: 0 nodes on new color (Karpenter provisions on demand), True = Ready
# flink-taskmanager-black   flink-taskmanager-black   0    True
# flink-taskmanager-red     flink-taskmanager-red     123  True
```

### Phase 4: Cordon Old Nodes

⚠️ **Critical**: `xargs kubectl` inside a bash subshell loses aws-okta credentials.
Wrap the entire command in `aws-okta exec <env>-admin -- bash -c '...'`:

```bash
# Cordon taskmanager-red nodes
aws-okta exec <env>-admin -- bash -c '
  kubectl --context=<env>-2-green get nodes \
    -l karpenter.sh/nodepool=flink-taskmanager-<old-color> -o name \
    | xargs kubectl --context=<env>-2-green cordon
'

# Cordon jobmanager-red nodes
aws-okta exec <env>-admin -- bash -c '
  kubectl --context=<env>-2-green get nodes \
    -l karpenter.sh/nodepool=flink-jobmanager-<old-color> -o name \
    | xargs kubectl --context=<env>-2-green cordon
'

# Verify: all should show SchedulingDisabled
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green \
  get nodes -l role=flink-taskmanager --no-headers | awk '{print $2}' | sort | uniq -c
```

### Phase 5: Deploy Inflate Pods

Calculate target: `current_node_count + 5` for each pool.

```bash
# Get current counts
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green get nodepools 2>&1 | grep flink

# Apply initial manifests (20 TM, 10 JM)
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green apply \
  -f ~/workspace/devops/adhoc/k8s/inflate-karpenter-nodepools/eks-2-<new-color>/flink-taskmanager-<new-color>.yaml
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green apply \
  -f ~/workspace/devops/adhoc/k8s/inflate-karpenter-nodepools/eks-2-<new-color>/flink-jobmanager-<new-color>.yaml

# Scale in ~30% increments toward target (example: 122 TM → target 127, 11 JM → target 16)
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green scale deployment \
  inflate-flink-taskmanager-<new-color> --replicas=50
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green scale deployment \
  inflate-flink-jobmanager-<new-color> --replicas=6

aws-okta exec <env>-admin -- kubectl --context=<env>-2-green scale deployment \
  inflate-flink-taskmanager-<new-color> --replicas=90
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green scale deployment \
  inflate-flink-jobmanager-<new-color> --replicas=12

aws-okta exec <env>-admin -- kubectl --context=<env>-2-green scale deployment \
  inflate-flink-taskmanager-<new-color> --replicas=127   # final target
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green scale deployment \
  inflate-flink-jobmanager-<new-color> --replicas=16

# Verify all Ready
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green \
  get nodes -l karpenter.sh/nodepool=flink-taskmanager-<new-color> --no-headers \
  | awk '{print $2}' | sort | uniq -c
```

### Phase 6: Get Cluster URLs and Baseline Job Count

```bash
# Get flink ingress URLs
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green \
  get ingress -A -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.spec.rules[0].host}{"\n"}{end}' \
  | grep -E 'flink-la|flink-app'

# Baseline job count
aws-okta exec <env>-admin -- mistcli flink <env> jobs count
```

### Phase 7: Migrate Jobs

⚠️ **`--watch` flag does NOT exist on `move-all-jobs`** — omit it.

First check which operator types are present:
```bash
# Check for flink-app namespaces
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green get ns 2>&1 | grep flink-app
```

```bash
# Move la jobs (prompts for confirmation)
aws-okta exec <env>-admin -- mistcli flink operator la <env> green move-all-jobs
# Type: yes

# Move app jobs if flink-app namespaces exist
aws-okta exec <env>-admin -- mistcli flink operator app <env> green move-all-jobs
# Type: yes
```

For `flink-app` clusters, also drain their nodes:
```bash
# Get nodes running flink-app taskmanagers
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green \
  get pods -n flink-app-12xlarge-1-green --no-headers -o wide \
  | awk '{print $7}' | sort -u

# Drain them one by one
aws-okta exec <env>-admin -- bash -c '
  for node in <node1> <node2> ...; do
    kubectl --context=<env>-2-green drain $node \
      --ignore-daemonsets --delete-emptydir-data
  done
'
```

### Phase 8: Monitor Migration

Use `migration-status --watch` (separate command, works correctly):

```bash
aws-okta exec <env>-admin -- mistcli flink operator la <env> green migration-status --watch
```

Wait until output shows:
```
Status: COMPLETE (on backup)
• Jobs running on backup: 736 (100.0%)
• Jobs failed: 0
```

> ⚠️ The monitor script `~/workspace/devops/adhoc/k8s/scripts/monitor-flink-migration.sh` may not exist — use `migration-status --watch` instead.

### Phase 9: Restart JobManagers (Primary Only)

⚠️ **`kubectl rollout restart -l <label>` is NOT supported** — must get deployment names first.
⚠️ **Only restart PRIMARY namespaces** (not backup).

```bash
# Restart jobmanagers in each primary namespace
aws-okta exec <env>-admin -- bash -c '
for ns in flink-la-10xlarge-1-green flink-la-10xlarge-2-green flink-la-11xlarge-1-green \
          flink-la-12xlarge-1-green flink-la-12xlarge-2-green flink-la-12xlarge-3-green \
          flink-la-12xlarge-4-green flink-la-12xlarge-5-green flink-la-8xlarge-1-green \
          flink-la-8xlarge-2-green; do
  deploys=$(kubectl --context=<env>-2-green get deployment -n $ns -l component=jobmanager -o name 2>/dev/null)
  if [ -n "$deploys" ]; then
    echo "$deploys" | xargs kubectl --context=<env>-2-green rollout restart -n $ns && echo "✓ $ns"
  fi
done
'
```

Get the namespace list dynamically:
```bash
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green get ns -o name \
  | grep flink-la | grep -v backup | sed 's|namespace/||'
```

Monitor until all primary jobmanagers are Running:
```bash
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green \
  get pods -A -l component=jobmanager --no-headers \
  | grep -v backup | awk '{print $4}' | sort | uniq -c
# Target: all Running, none Pending/ContainerCreating
```

⚠️ **CRITICAL — Check for Karpenter re-provisioning new red nodes:**

After jobmanagers are evicted/restarted, Karpenter may provision **new uncordoned red nodes** to satisfy
the scheduling request (before the pods land on black). If this happens, the jobmanagers land on red again.

```bash
# Check for uncordoned red jobmanager nodes
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green \
  get nodes -l karpenter.sh/nodepool=flink-jobmanager-red --no-headers \
  | grep -v SchedulingDisabled

# If any appear — drain them to force pods onto black:
aws-okta exec <env>-admin -- bash -c '
  kubectl --context=<env>-2-green get nodes \
    -l karpenter.sh/nodepool=flink-jobmanager-red --no-headers \
    | grep -v SchedulingDisabled | awk "{print \$1}" | while read node; do
      echo "Draining $node..."
      kubectl --context=<env>-2-green drain $node \
        --ignore-daemonsets --delete-emptydir-data
  done
'
```

Verify jobmanagers are actually on **black** nodes (do not skip this):
```bash
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green get nodepools 2>&1 | grep flink-jobmanager
# flink-jobmanager-black should be > 0, flink-jobmanager-red should drain toward 0

# Double-check pod placement
aws-okta exec <env>-admin -- bash -c '
  kubectl --context=<env>-2-green get pods -A -l component=jobmanager \
    --no-headers | grep -v backup | awk "{print \$1, \$7}" | while read ns node; do
    pool=$(kubectl --context=<env>-2-green get node $node \
      -o jsonpath="{.metadata.labels[\"karpenter.sh/nodepool\"]}" 2>/dev/null)
    echo "$ns → $node ($pool)"
  done
'
# All should show flink-jobmanager-black
```

### Phase 10: Move Jobs Back

```bash
aws-okta exec <env>-admin -- mistcli flink operator la <env> green move-all-jobs-back
# Type: yes

# Watch until complete
aws-okta exec <env>-admin -- mistcli flink operator la <env> green migration-status --watch
```

Wait for:
```
Status: COMPLETE (on primary)
• Jobs running on primary: 736 (100.0%)
```

### Phase 11: Finalize

```bash
# 1. Delete inflate deployments
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green \
  delete deployment inflate-flink-taskmanager-<new-color> inflate-flink-jobmanager-<new-color> \
  -n default

# 2. Final job count verification
aws-okta exec <env>-admin -- mistcli flink <env> jobs count

# 3. Add completion comment to PR
cd ~/workspace/mist-sk8r
gh pr comment <PR_NUMBER> --body "## AMI Upgrade Complete ✅
**Environment**: AWS <env>
**Date**: $(date -u '+%Y-%m-%d %H:%M UTC')
**Jobs migrated**: <N> total (0 failed)
**Old nodepools**: drained (0 nodes)
Migration completed successfully with zero job failures."
```

### Phase 12: Cleanup Old Node Pools (AWS)

For AWS, **Karpenter auto-terminates empty cordoned nodes** via consolidation policy (`WhenEmpty`, 15m delay). No manual deletion required.

```bash
# Verify old red nodes have drained to 0 automatically
aws-okta exec <env>-admin -- kubectl --context=<env>-2-green get nodepools 2>&1 | grep flink
# Expected: flink-taskmanager-red = 0, flink-jobmanager-red = 0
```

> No GKE/Terragrunt cleanup needed for AWS. NodePool CRD objects remain but have 0 nodes.

---

## Common Pitfalls & Fixes

| Issue | Fix |
|-------|-----|
| `xargs kubectl` — Unauthorized | Wrap entire command in `aws-okta exec <env>-admin -- bash -c '...'` |
| `kubectl rollout restart -l` — unknown flag | Get deployment names first: `kubectl get deploy -l component=jobmanager -o name \| xargs kubectl rollout restart` |
| `move-all-jobs --watch` — unknown flag | Remove `--watch`; monitor separately with `migration-status --watch` |
| Monitor script not found | Use `mistcli flink operator la <env> green migration-status --watch` |
| Only 2/10 namespaces restart in loop | Wrap loop in `aws-okta exec <env>-admin -- bash -c '...'` to share credentials |
| Backup jobmanagers restarted by mistake | Filter namespace list with `grep -v backup` |
| Red nodes not draining | They drain automatically via Karpenter consolidation; wait ~15min |
| Jobmanagers land on new red nodes after restart | Karpenter provisions new red nodes before pods schedule to black — cordon and drain those new red nodes |
| flink-app clusters also present | Check `kubectl get ns \| grep flink-app`; run `move-all-jobs` for `app` type too and drain app nodes separately |

## Quick Reference — EU Environment

```bash
ENV=eu
CONTEXT=eu-2-green
PROFILE=eu-admin
MIST_SK8R=~/workspace/mist-sk8r/eu-2-green.eks
KARPENTER_NP_PATH=$MIST_SK8R/config/flux-monitored/karpenter-nodepools
INFLATE_PATH=~/workspace/devops/adhoc/k8s/inflate-karpenter-nodepools/eks-2-black

# All kubectl commands:
aws-okta exec eu-admin -- kubectl --context=eu-2-green <cmd>

# All mistcli commands:
aws-okta exec eu-admin -- mistcli flink eu <cmd>
aws-okta exec eu-admin -- mistcli flink operator la eu green <cmd>
```
