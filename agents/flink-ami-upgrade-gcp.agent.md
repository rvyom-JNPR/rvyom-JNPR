---
description: |
  Use this agent for GCP-specific Flink GKE version upgrades. Incorporates
  lessons from aso1prod5 session (2026-07-23). Handles the full 12-phase
  red-black node pool rotation workflow for GKE/Terragrunt environments.

  Trigger phrases:
  - 'upgrade Flink node pools for GKE version update'
  - 'flink AMI upgrade gcp'
  - 'migrate flink jobs for GKE upgrade'
  - 'start ami upgrade for gcp-<env>'

  Examples:
  - 'Upgrade Flink node pools in aso1prod5-gcp for the new GKE version'
  - 'Start ami upgrade for gcp-mec2prod6'
  - 'Let's upgrade Flink in usc1prod4-gcp'
name: flink-ami-upgrade-gcp
tools: ['execute', 'read', 'search', 'edit', 'todo']
argument-hint: "GCP environment name (e.g., aso1prod5, mec2prod6, usc1prod4)"
---

# Flink AMI Upgrade Agent — GCP (GKE/Terragrunt)

Handles zero-downtime Flink GKE version upgrades using red-black node pool rotation on GCP.

## Key Repositories

- **IAC**: `~/workspace/iac` — Terragrunt configs (`<env>-gcp/auto/gke-2-green/gke_config.hcl`)
- **devops**: `~/workspace/devops` — inflate pod manifests (`adhoc/k8s/inflate-gke-nodepools/gke-2/`)

## Environment Naming

| Input format | IAC directory | kubectl context |
|---|---|---|
| `aso1prod5` or `gcp-aso1prod5` | `aso1prod5-gcp/auto/gke-2-green/` | `gcp-aso1prod5-2-green` |
| `mec2prod6` or `gcp-mec2prod6` | `mec2prod6-gcp/auto/gke-2-green/` | `gcp-mec2prod6-2-green` |

Normalize: strip `gcp-` prefix if present. Config path: `~/workspace/iac/<env>-gcp/auto/gke-2-green/gke_config.hcl`

## GCP Authentication

```bash
# Strip -gcp suffix for service account name
# e.g. aso1prod5-gcp → aso1prod5
# Exception: gcp-production → prod
SHORT_ENV=aso1prod5
export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token \
  --impersonate-service-account terraform-create-${SHORT_ENV}@mist-infrastructure-iam.iam.gserviceaccount.com)
```

## Node Pool Color Convention

Red-black deployment: always create the **opposite** color of what currently exists in `node_pools`.
- Current `*-red` → create `*-black`
- Current `*-black` → create `*-red`

## Upgrade Workflow

### Phase 1: Discover Current State

```bash
ENV=aso1prod5
CONTEXT=gcp-${ENV}-2-green
CONFIG=~/workspace/iac/${ENV}-gcp/auto/gke-2-green/gke_config.hcl

# Determine active color (which flink pools exist)
grep "name.*flink-" $CONFIG

# Check live node versions
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager --no-headers | awk '{print $1, $5}' | head -3
kubectl --context $CONTEXT get nodes -l role=flink-jobmanager --no-headers | awk '{print $1, $5}' | head -3
```

Ask user for the new GKE version (e.g., `1.33.12-gke.1270000`).

---

### Phase 2: Create New Node Pools (IAC)

**CRITICAL**: Always branch from `origin/main`, never from another feature branch.

```bash
cd ~/workspace/iac
git fetch origin
git checkout -b flink-nodepool/${ENV}-black-$(date +%Y%m%d) origin/main
```

Edit `${ENV}-gcp/auto/gke-2-green/gke_config.hcl` — add new `<color>` node pool blocks after the existing flink pools:

```hcl
{
  name                 = "flink-jobmanager-<color>"
  version              = "<new-gke-version>"
  machine_type         = "n2d-standard-8"    # match existing jobmanager pool
  initial_node_count   = 0
  min_count            = 0
  max_count            = 24                   # match existing
  preemptible          = false
  spot                 = false
  enable_private_nodes = true
  node_metadata        = "GKE_METADATA"
  disk_size_gb         = 100
  disk_type            = "pd-balanced"
  auto_repair          = true
  auto_upgrade         = true
  max_pods_per_node    = 64
},

{
  name                 = "flink-taskmanager-<color>"
  version              = "<new-gke-version>"
  machine_type         = "n2d-standard-16"   # match existing taskmanager pool
  initial_node_count   = 0
  min_count            = 0
  max_count            = 80                   # match existing
  preemptible          = false
  spot                 = false
  enable_private_nodes = true
  node_metadata        = "GKE_METADATA"
  disk_size_gb         = 150
  disk_type            = "pd-balanced"
  auto_repair          = true
  auto_upgrade         = true
  max_pods_per_node    = 64
},
```

> ⚠️ Labels, taints, and oauth_scopes for `*-black` entries are already present in the config. Only add the `node_pools` blocks.

**Commit — stage only the relevant file:**
```bash
git add ${ENV}-gcp/auto/gke-2-green/gke_config.hcl
git commit -m "feat(${ENV}): Add flink-{jobmanager,taskmanager}-<color> node pools (<new-version>)

MIST-XXXXXX"
git push -u origin HEAD
```

**Create PR:**
```bash
gh pr create \
  --title "feat(${ENV}): Add flink-{jobmanager,taskmanager}-<color> node pools (<new-version>)" \
  --body "## Summary
Add new <color> Flink node pools for GKE version upgrade via red-black rotation.

## Changes
- Added \`flink-jobmanager-<color>\` (n2d-standard-8, max 24, version <new-version>)
- Added \`flink-taskmanager-<color>\` (n2d-standard-16, max 80, version <new-version>)

## Current state
- Active pools: \`flink-jobmanager-<old-color>\` / \`flink-taskmanager-<old-color>\` @ <old-version>

## Jira
https://mistsys.atlassian.net/browse/MIST-XXXXXX"
```

---

### Phase 2b: Terragrunt Plan & Apply

```bash
cd ~/workspace/iac/${ENV}-gcp/auto/gke-2-green

export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token \
  --impersonate-service-account terraform-create-${ENV}@mist-infrastructure-iam.iam.gserviceaccount.com)

terragrunt plan 2>&1 | tail -60
# Verify: "Plan: 4 to add, 0 to change, 0 to destroy" (2 node pools + 2 random_id resources)

# After confirmation:
terragrunt apply -auto-approve 2>&1 | tail -20
```

> ⚠️ **Auth note**: Export `GOOGLE_OAUTH_ACCESS_TOKEN` in the **same shell** as `terragrunt`. It does not persist across subshells.

---

### Phase 3: Verify New Node Pools

```bash
CONTEXT=gcp-${ENV}-2-green

# Verify new black nodes (min=0 so none running yet — that's expected)
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager --no-headers
kubectl --context $CONTEXT get nodes -l role=flink-jobmanager --no-headers
```

Zero nodes is expected since both pools start with `min_count=0`. They scale up after inflate pods.

---

### Phase 4: Cordon Old Nodes

```bash
# Cordon old color nodes (prevents new scheduling)
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager -o name | \
  xargs kubectl --context $CONTEXT cordon

kubectl --context $CONTEXT get nodes -l role=flink-jobmanager -o name | \
  xargs kubectl --context $CONTEXT cordon

# Verify — should show Ready,SchedulingDisabled
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager --no-headers | awk '{print $1, $2}' | head -5
kubectl --context $CONTEXT get nodes -l role=flink-jobmanager --no-headers | awk '{print $1, $2}' | head -5
```

> Note: This cordons **all** flink nodes (both colors if both exist). That's safe — the new black nodes are already cordoned since they have 0 nodes and the inflate pods haven't been applied yet. After inflate runs, only the old red nodes will be SchedulingDisabled.

---

### Phase 5: Deploy Inflate Pods

Inflate pods force GKE CA to pre-scale the new node pools before migration.

**Locate manifests:**
```bash
ls ~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/
# flink-jobmanager-black.yaml  flink-taskmanager-black.yaml
# flink-jobmanager-red.yaml    flink-taskmanager-red.yaml
```

**Count current nodes to set replicas:**
```bash
# Count old-color nodes
TM_COUNT=$(kubectl --context $CONTEXT get nodes -l role=flink-taskmanager --no-headers | grep -v SchedulingDisabled | wc -l)
JM_COUNT=$(kubectl --context $CONTEXT get nodes -l role=flink-jobmanager --no-headers | grep -v SchedulingDisabled | wc -l)
echo "taskmanager: $TM_COUNT, jobmanager: $JM_COUNT"
```

Update replicas in the manifest files to match the old node counts, then apply:

```bash
# Apply inflate pods for the NEW color
kubectl --context $CONTEXT apply -f ~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/flink-jobmanager-<new-color>.yaml
kubectl --context $CONTEXT apply -f ~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/flink-taskmanager-<new-color>.yaml

# Verify pods are pending (GKE CA will provision new nodes)
kubectl --context $CONTEXT get pods -n default -l 'app in (inflate-flink-jobmanager-<new-color>,inflate-flink-taskmanager-<new-color>)' --no-headers | awk '{print $3}' | sort | uniq -c
```

> ⚠️ **Init pool memory pressure**: If the autoscaler or other workloads go Pending with `1 max node group size reached`, the `init-red` pool may be full. Bump its `max_count` in `gke_config.hcl` (e.g., 5→7) and apply terragrunt. See [Known Issues](#known-issues).

---

### Phase 6: Discover Cluster URLs

```bash
# Get all Flink ingress URLs
kubectl --context $CONTEXT get ingress -A \
  -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.spec.rules[0].host}{"\n"}{end}' \
  | grep -E 'flink-la|flink-app' | sort

# Primary clusters only
kubectl --context $CONTEXT get ingress -A \
  -o jsonpath='{range .items[*]}{.spec.rules[0].host}{"\n"}{end}' \
  | grep -E 'flink-la|flink-app' | grep -v backup | sort
```

Note both `la` (location analytics) and `app` operator types — both must be migrated separately.

---

### Phase 7: Migrate Jobs to Backup

```bash
# Move LA jobs to backup
mistcli flink operator la ${ENV} green move-all-jobs
# → prompts "Are you sure? (yes/no)" — type: yes

# Move App jobs to backup
mistcli flink operator app ${ENV} green move-all-jobs
# → prompts "Are you sure? (yes/no)" — type: yes
```

---

### Phase 8: Monitor Migration

```bash
# Check LA migration progress
mistcli flink operator la ${ENV} green migration-status

# Check App migration progress
mistcli flink operator app ${ENV} green migration-status

# Watch mode (auto-refresh every 5s)
mistcli flink operator la ${ENV} green migration-status --watch
```

**Expected output when complete:**
```
• Jobs running on primary:  0 (0.0%)
• Jobs running on backup:   736 (100.0%)
```

> ⚠️ `mistcli flink <env> jobs count` does NOT work for all environments (no built-in cluster list for some envs). Use `migration-status` instead.
> ⚠️ No monitor script exists at `~/workspace/devops/adhoc/k8s/scripts/monitor-flink-migration.sh` — it's a placeholder only.

---

### Phase 9: Restart Primary JobManagers

Once jobs are fully on backup, restart jobmanager pods in **primary clusters only** so they reschedule onto the new node pool.

```bash
# Check current node pool placement of primary jobmanagers
kubectl --context $CONTEXT get pods -A -l component=jobmanager --no-headers | grep -v backup \
  | awk '{print $1, $2}' | while read ns pod; do
    node=$(kubectl --context $CONTEXT get pod $pod -n $ns -o jsonpath='{.spec.nodeName}' 2>/dev/null)
    pool=$(kubectl --context $CONTEXT get node $node -o jsonpath='{.metadata.labels.node_pool}' 2>/dev/null)
    pstatus=$(kubectl --context $CONTEXT get pod $pod -n $ns --no-headers 2>/dev/null | awk '{print $3}')
    echo "$ns | $pstatus | $pool"
  done

# Delete primary jobmanager pods still on old color
kubectl --context $CONTEXT get pods -A -l component=jobmanager --no-headers | grep -v backup \
  | awk '{print $1, $2}' | while read ns pod; do
    node=$(kubectl --context $CONTEXT get pod $pod -n $ns -o jsonpath='{.spec.nodeName}' 2>/dev/null)
    pool=$(kubectl --context $CONTEXT get node $node -o jsonpath='{.metadata.labels.node_pool}' 2>/dev/null)
    if [[ "$pool" == "flink-jobmanager-<old-color>" ]]; then
      echo "Deleting $pod in $ns (on $pool)"
      kubectl --context $CONTEXT delete pod $pod -n $ns
    fi
  done
```

> ⚠️ **zsh variable name**: Do NOT use `status` as a variable name — it is read-only in zsh. Use `pstatus` instead.
> ⚠️ Do NOT delete jobmanagers in backup namespaces — only primary (grep -v backup).

**Verify all primary jobmanagers on new color:**
```bash
kubectl --context $CONTEXT get pods -A -l component=jobmanager --no-headers | grep -v backup \
  | awk '{print $1, $2}' | while read ns pod; do
    node=$(kubectl --context $CONTEXT get pod $pod -n $ns -o jsonpath='{.spec.nodeName}' 2>/dev/null)
    pool=$(kubectl --context $CONTEXT get node $node -o jsonpath='{.metadata.labels.node_pool}' 2>/dev/null)
    pstatus=$(kubectl --context $CONTEXT get pod $pod -n $ns --no-headers 2>/dev/null | awk '{print $3}')
    echo "$ns | $pstatus | $pool"
  done
# All lines should show: Running | flink-jobmanager-<new-color>
```

---

### Phase 10: Move Jobs Back to Primary

```bash
# Move LA jobs back
mistcli flink operator la ${ENV} green move-all-jobs-back
# → prompts "Are you sure? (yes/no)" — type: yes

# Move App jobs back
mistcli flink operator app ${ENV} green move-all-jobs-back
# → prompts "Are you sure? (yes/no)" — type: yes

# Monitor until complete
mistcli flink operator la ${ENV} green migration-status
mistcli flink operator app ${ENV} green migration-status
# Target: Jobs running on primary: 100%, backup: 0%
```

---

### Phase 11: Drain Old Nodes

```bash
# Check what's still on old-version nodes
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager --no-headers | grep "<old-version>"

# Verify no flink workloads remain on old nodes
kubectl --context $CONTEXT get pods -A -l component=taskmanager --no-headers \
  | awk '{print $1, $2}' | while read ns pod; do
    node=$(kubectl --context $CONTEXT get pod $pod -n $ns -o jsonpath='{.spec.nodeName}' 2>/dev/null)
    pool=$(kubectl --context $CONTEXT get node $node -o jsonpath='{.metadata.labels.node_pool}' 2>/dev/null)
    if [[ "$pool" == *"-<old-color>" ]]; then echo "STILL ON OLD: $ns/$pod on $pool"; fi
  done

# Drain remaining old-color nodes
kubectl --context $CONTEXT get nodes -l role=flink-taskmanager --no-headers \
  | grep "<old-version>" | awk '{print $1}' \
  | xargs -I{} kubectl --context $CONTEXT drain {} \
      --ignore-daemonsets --delete-emptydir-data --force 2>&1

kubectl --context $CONTEXT get nodes -l role=flink-jobmanager --no-headers \
  | grep "<old-version>" | awk '{print $1}' \
  | xargs -I{} kubectl --context $CONTEXT drain {} \
      --ignore-daemonsets --delete-emptydir-data --force 2>&1
```

---

### Phase 12: Cleanup & PR Merge

**Step 1 — Scale down inflate pods:**
```bash
kubectl --context $CONTEXT scale deployment inflate-flink-jobmanager-<new-color> --replicas=0 -n default
kubectl --context $CONTEXT scale deployment inflate-flink-taskmanager-<new-color> --replicas=0 -n default
# Or delete them entirely:
kubectl --context $CONTEXT delete deployment inflate-flink-jobmanager-<new-color> -n default
kubectl --context $CONTEXT delete deployment inflate-flink-taskmanager-<new-color> -n default
```

**Step 2 — Remove old node pool from gke_config.hcl:**

Remove from `node_pools`:
```hcl
# DELETE these blocks:
{
  name = "flink-jobmanager-<old-color>"
  ...
},
{
  name = "flink-taskmanager-<old-color>"
  ...
},
```

Remove from `node_pools_labels`:
```hcl
# DELETE:
flink-jobmanager-<old-color>   = { role = "flink-jobmanager" }
flink-taskmanager-<old-color>  = { role = "flink-taskmanager" }
```

Remove from `node_pools_taints` (both jobmanager and taskmanager blocks for old color).

Remove from `node_pools_oauth_scopes`:
```hcl
# DELETE:
flink-taskmanager-<old-color> = [
  "https://www.googleapis.com/auth/userinfo.email"
]
```

**Step 3 — Commit, plan, apply:**
```bash
cd ~/workspace/iac
git add ${ENV}-gcp/auto/gke-2-green/gke_config.hcl
git commit -m "chore(${ENV}): Remove flink-{jobmanager,taskmanager}-<old-color> node pools

Migration to <new-color> complete.

MIST-XXXXXX"
git push

# Plan first — show the user before applying
cd ${ENV}-gcp/auto/gke-2-green
export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token \
  --impersonate-service-account terraform-create-${ENV}@mist-infrastructure-iam.iam.gserviceaccount.com)
terragrunt plan 2>&1 | grep -E "Plan:|will be destroyed|# google_container_node_pool"
# Expected: "Plan: 0 to add, 0 to change, 4 to destroy"

# After user confirmation:
terragrunt apply -auto-approve
```

**Step 4 — Add completion comment to PR:**
```bash
gh pr comment <PR_NUMBER> --body "## Upgrade Complete ✅
- Old version: \`<old-version>\` (<old-color>)
- New version: \`<new-version>\` (<new-color>)
- LA jobs on primary: X/X (100%)
- App jobs on primary: X/X (100%)
- Red node pools: Deleted"
```

**Step 5 — Merge PR when approved:**
```bash
gh pr merge <PR_NUMBER> --squash --delete-branch
```

---

## Known Issues

| Issue | Cause | Fix |
|---|---|---|
| Autoscaler pod Pending (`Insufficient memory`) | All init nodes are memory-saturated; init pool at `max_count` | Bump `init-red max_count` (e.g., 5→7) in `gke_config.hcl`, commit + apply terragrunt |
| `mistcli flink <env> jobs count` fails with "no built-in cluster list" | Some envs not in mistcli's hardcoded list | Use `mistcli flink operator la/app <env> green migration-status` instead |
| Monitor script not found | `~/workspace/devops/adhoc/k8s/scripts/monitor-flink-migration.sh` does not exist | Use `mistcli flink operator migration-status` or `--watch` flag |
| `zsh: read-only variable: status` | `status` is reserved in zsh | Use `pstatus` or `pod_state` as variable name |
| Terragrunt auth fails with personal identity (403 on state bucket) | `GOOGLE_OAUTH_ACCESS_TOKEN` not exported in the same shell | Run `export GOOGLE_OAUTH_ACCESS_TOKEN=...` and `terragrunt plan/apply` in the same shell/command |
| Branch carries unrelated commits | Branch created from a feature branch instead of main | Always: `git checkout -b <branch> origin/main` |
| Red nodes still showing in GCP console | Drain evicts pods but GKE CA takes time to terminate nodes | Wait a few minutes; verify with `kubectl get nodes` |

## Inflate Pod Manifest Locations

```
~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/
├── flink-jobmanager-black.yaml
├── flink-jobmanager-red.yaml
├── flink-taskmanager-black.yaml
└── flink-taskmanager-red.yaml
```

The `replicas` field in each file reflects the **last applied count**. Always verify the current count against live nodes before applying.

## Checklist

- [ ] Determine current active color from `gke_config.hcl`
- [ ] Get new GKE version from user
- [ ] Create branch from `origin/main` (`git checkout -b ... origin/main`)
- [ ] Add new color node pool blocks to `gke_config.hcl`
- [ ] Stage only `gke_config.hcl` and commit
- [ ] Push and create PR with Jira link
- [ ] Run terragrunt plan — verify 4 to add
- [ ] Run terragrunt apply
- [ ] Verify new node pools exist (0 nodes expected at this stage)
- [ ] Cordon all old-color flink nodes
- [ ] Apply inflate pods for new color
- [ ] Discover all cluster URLs (la + app operators)
- [ ] Run `mistcli flink operator la <env> green move-all-jobs` (confirm yes)
- [ ] Run `mistcli flink operator app <env> green move-all-jobs` (confirm yes)
- [ ] Monitor until 100% on backup
- [ ] Delete primary jobmanager pods (grep -v backup, use `pstatus` variable)
- [ ] Verify all primary jobmanagers Running on new color
- [ ] Run `mistcli flink operator la <env> green move-all-jobs-back` (confirm yes)
- [ ] Run `mistcli flink operator app <env> green move-all-jobs-back` (confirm yes)
- [ ] Monitor until 100% on primary
- [ ] Drain old-color nodes (`--ignore-daemonsets --delete-emptydir-data --force`)
- [ ] Verify no flink workloads on old nodes
- [ ] Scale down / delete inflate pods
- [ ] Remove old color blocks from `gke_config.hcl` (node_pools, labels, taints, oauth_scopes)
- [ ] Commit cleanup, push, terragrunt plan (show user first)
- [ ] Terragrunt apply to delete old node pools
- [ ] Add completion comment to PR
- [ ] Merge PR when approved
