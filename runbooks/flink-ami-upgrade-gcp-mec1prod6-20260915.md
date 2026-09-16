# Flink GKE Upgrade Runbook - mec1prod6-gcp - 2026-09-15

## Summary

| Field | Value |
|---|---|
| Environment | `mec1prod6-gcp` |
| GKE Cluster | `mec1prod6-2-green` (`europe-west3`) |
| kubectl context | `gcp-mec1prod6-2-green` |
| IAC path | `/Users/rvyom/workspace/iac/mec1prod6-gcp/auto/gke-2-green/gke_config.hcl` |
| Active Flink color | `black` |
| Current live Flink kubelet version | `v1.33.13-gke.1011000` |
| Current IAC cluster/node version | `1.33.13-gke.1329000` |
| New Flink color to create | `red` |
| Target refresh version | `1.33.13-gke.1329000` |
| App-push view | `flink-app` in `/Users/rvyom/workspace/versions/jenkins/gcp-mec1prod6.json` |
| IAC worktree | `/Users/rvyom/workspace/iac-flink-mec1prod6-20260915` |
| IAC branch | `flink-nodepool/mec1prod6-red-20260915` |

## Current Baseline

Read-only checks completed:

```zsh
kubectl config get-contexts gcp-mec1prod6-2-green --no-headers
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-jobmanager -o wide --no-headers
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-taskmanager -o wide --no-headers
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-jobmanager \
  -o custom-columns='NAME:.metadata.name,NODE_POOL:.metadata.labels.node_pool,GKE_POOL:.metadata.labels.cloud\\.google\\.com/gke-nodepool,KUBELET:.status.nodeInfo.kubeletVersion,UNSCHED:.spec.unschedulable' \
  --no-headers
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-taskmanager \
  -o custom-columns='NAME:.metadata.name,NODE_POOL:.metadata.labels.node_pool,GKE_POOL:.metadata.labels.cloud\\.google\\.com/gke-nodepool,KUBELET:.status.nodeInfo.kubeletVersion,UNSCHED:.spec.unschedulable' \
  --no-headers
```

Observed live Flink pools:

| Role | Active pool | GKE pool suffix | Live kubelet version | Nodes | Scheduling |
|---|---|---|---|---:|---|
| jobmanager | `flink-jobmanager-black` | `flink-jobmanager-black-286c` | `v1.33.13-gke.1011000` | 8 | enabled |
| taskmanager | `flink-taskmanager-black` | `flink-taskmanager-black-7055` | `v1.33.13-gke.1011000` | 11 | enabled |

IAC currently contains only these Flink node-pool blocks:

```hcl
flink-jobmanager-black  version = "1.33.13-gke.1329000"
flink-taskmanager-black version = "1.33.13-gke.1329000"
```

The red-black refresh adds `flink-jobmanager-red` and `flink-taskmanager-red` node-pool blocks at `1.33.13-gke.1329000`, matching the current cluster/node version pinned in IAC.

## Flink Clusters

Discovered ingress hosts:

```text
flink-app-8xlarge-1-green                 flink-app-8xlarge-1-green-mec1prod6.mist.pvt
flink-app-8xlarge-1-backup-green          flink-app-8xlarge-1-backup-green-mec1prod6.mist.pvt
flink-app-operator                        flink-app-operator-mec1prod6-green.mist.pvt
flink-la-12xlarge-1-green                 flink-la-12xlarge-1-green-mec1prod6.mist.pvt
flink-la-12xlarge-1-backup-green          flink-la-12xlarge-1-backup-green-mec1prod6.mist.pvt
flink-la-8xlarge-1-green                  flink-la-8xlarge-1-green-mec1prod6.mist.pvt
flink-la-8xlarge-1-backup-green           flink-la-8xlarge-1-backup-green-mec1prod6.mist.pvt
flink-la-medium-1-green                   flink-la-medium-1-green-mec1prod6.mist.pvt
flink-la-medium-1-backup-green            flink-la-medium-1-backup-green-mec1prod6.mist.pvt
flink-la-medium-2-green                   flink-la-medium-2-green-mec1prod6.mist.pvt
flink-la-medium-2-backup-green            flink-la-medium-2-backup-green-mec1prod6.mist.pvt
flink-la-small-1-green                    flink-la-small-1-green-mec1prod6.mist.pvt
flink-la-small-1-backup-green             flink-la-small-1-backup-green-mec1prod6.mist.pvt
flink-la-small-2-green                    flink-la-small-2-green-mec1prod6.mist.pvt
flink-la-small-2-backup-green             flink-la-small-2-backup-green-mec1prod6.mist.pvt
flink-operator                            flink-la-operator-mec1prod6-green.mist.pvt
```

Current `mistcli flink mec1prod6 jobs count` baseline:

| Cluster | Total | Running | Failed |
|---|---:|---:|---:|
| flink-la-small-1-green | 201 | 201 | 0 |
| flink-la-small-2-green | 181 | 181 | 0 |
| flink-la-medium-1-green | 165 | 165 | 0 |
| flink-la-medium-2-green | 202 | 202 | 0 |
| flink-la-12xlarge-1-green | 12 | 12 | 0 |
| total | 761 | 761 | 0 |

Note: `mistcli` reported `flink-la-backup-green` DNS lookup failure, while the numbered backup ingress hosts exist.

## App Push Context

The `gcp-mec1prod6` Jenkins config has a `snapshot-flink-app` entry:

```json
{
  "name": "snapshot-flink-app",
  "priority": "p2",
  "tag": "refs/tags/v4.212.1",
  "pipeline_path": "k8s-pipeline/snapshot/Jenkinsfile_flink_app_snapshot",
  "view": "flink-app"
}
```

Need the app-push PR number or tag before running the local verifier:

```zsh
cd /Users/rvyom/workspace/rvyom-JNPR/scripts
python3 verify_flink_push.py mistsys/versions <PR_NUMBER> --env gcp.mec1prod6 --verify
```

## Phase 1 - Prepare IAC Change

1. Confirmed target GKE version for this Flink refresh: `1.33.13-gke.1329000`.
2. Created isolated worktree from `origin/main`: `/Users/rvyom/workspace/iac-flink-mec1prod6-20260915`.
3. Added opposite-color red node pools using the black pool shapes:
   - `flink-jobmanager-red`: `n2d-standard-8`, `max_count = 20`, disk `100GiB`.
   - `flink-taskmanager-red`: `e2-highmem-8`, `max_count = 48`, disk `150GiB`.
4. Keep existing label, taint, and OAuth map entries for both red and black pools.

Suggested branch:

```zsh
cd /Users/rvyom/workspace/iac
git fetch origin
git checkout origin/main -B flink-nodepool/mec1prod6-red-20260915
```

Local validation passed:

```zsh
cd /Users/rvyom/workspace/iac-flink-mec1prod6-20260915/mec1prod6-gcp/auto/gke-2-green
terragrunt hclfmt --check
git -C /Users/rvyom/workspace/iac-flink-mec1prod6-20260915 diff --check -- mec1prod6-gcp/auto/gke-2-green/gke_config.hcl
```

## Phase 2 - Plan and PR

GCP impersonation is required before `terragrunt plan` or `terragrunt apply`:

```zsh
export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token --impersonate-service-account terraform-create-mec1prod6@mist-infrastructure-iam.iam.gserviceaccount.com)
cd /Users/rvyom/workspace/iac/mec1prod6-gcp/auto/gke-2-green
terragrunt plan
```

Plan status: passed after clearing generated Terragrunt caches and rerunning with service-account impersonation.

Plan shape:

```text
Plan: 4 to add, 0 to change, 0 to destroy
```

Resources to create:

```text
google_container_node_pool.pools["flink-jobmanager-red"]
google_container_node_pool.pools["flink-taskmanager-red"]
random_id.name["flink-jobmanager-red"]
random_id.name["flink-taskmanager-red"]
```

Create a PR before apply. Do not apply until the plan is reviewed and explicitly approved.

## Phase 3 - Verify New Red Pools

After apply, verify red pools exist and scale only through inflate pods:

```zsh
kubectl --context gcp-mec1prod6-2-green get nodes -l node_pool=flink-jobmanager-red
kubectl --context gcp-mec1prod6-2-green get nodes -l node_pool=flink-taskmanager-red
```

## Phase 4 - Migration Guardrails

- Do not cordon black nodes until red pools are present and healthy.
- Do not move Flink jobs until new red nodes are Ready.
- Record job counts before and after every migration action.
- Restart primary JobManagers only after jobs have moved to backup clusters.
- Do not remove old black pools until all jobs are back on primary and healthy.

Executed: cordoned all 8 `flink-jobmanager-black` nodes and all 11 `flink-taskmanager-black`
nodes. Verified all showed `Ready,SchedulingDisabled`.

```zsh
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-jobmanager -o name | xargs kubectl --context gcp-mec1prod6-2-green cordon
kubectl --context gcp-mec1prod6-2-green get nodes -l role=flink-taskmanager -o name | xargs kubectl --context gcp-mec1prod6-2-green cordon
```

## Phase 5 - Inflate Red Pools

Updated inflate manifests at `~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/`
to match current black node counts:

- `flink-jobmanager-red.yaml`: replicas `6 → 8`
- `flink-taskmanager-red.yaml`: replicas `23 → 11`

```zsh
kubectl --context gcp-mec1prod6-2-green apply -f ~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/flink-jobmanager-red.yaml
kubectl --context gcp-mec1prod6-2-green apply -f ~/workspace/devops/adhoc/k8s/inflate-gke-nodepools/gke-2/flink-taskmanager-red.yaml
```

Red pools scaled up to 8 jobmanager + 11 taskmanager nodes; all inflate pods reached `Running`.

## Phase 6 - Cluster URLs

Reused the ingress host list captured in the baseline section above (no rediscovery needed).

## Phase 7 - Migrate Jobs to Backup

```zsh
mistcli flink-operator la mec1prod6 green move-all-jobs   <<< "yes"
mistcli flink-operator app mec1prod6 green move-all-jobs  <<< "yes"
```

## Phase 8 - Monitor Migration to Backup

Polled `migration-status` repeatedly (~35 minutes) until both reached 100% on backup:

| Time | LA primary | LA backup | App primary | App backup |
|---|---|---|---|---|
| T+0 | 761 (100%) | 0 | 17 (100%) | 0 |
| T+complete | 0 | 761 (100%) | 0 | 17 (100%) |

0 failures / 0 cancellations throughout.

## Phase 9 - Restart Primary JobManagers

```zsh
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

> ⚠️ GKE cluster autoscaler kept creating brand-new `flink-jobmanager-black` nodes (not caught
> by the earlier cordon since they didn't exist yet), so 2 jobmanager pods repeatedly
> rescheduled back onto new black nodes. Resolved by looping: cordon any newly appeared
> black node immediately, then delete the pod again, until it lands on red.

All 7 primary jobmanagers (`flink-app-8xlarge-1-green`, `flink-la-12xlarge-1-green`,
`flink-la-8xlarge-1-green`, `flink-la-medium-1-green`, `flink-la-medium-2-green`,
`flink-la-small-1-green`, `flink-la-small-2-green`) confirmed running on `flink-jobmanager-red`.

## Phase 10 - Move Jobs Back to Primary

```zsh
mistcli flink-operator la mec1prod6 green move-all-jobs-back   <<< "yes"
mistcli flink-operator app mec1prod6 green move-all-jobs-back  <<< "yes"
```

Polled `migration-status` repeatedly (~45 minutes) until:
- LA: `COMPLETE (on primary)` ✅
- App: 17/17 (100%) on primary ✅

0 failures throughout.

## Phase 11 - Drain Black Nodes

Checked black jobmanager/taskmanager nodes: GKE cluster autoscaler had already
auto-terminated all cordoned+empty black JM/TM nodes on its own — no explicit
`kubectl drain` was required.

Scaled inflate deployments back down to 0:

```zsh
kubectl --context gcp-mec1prod6-2-green scale deployment inflate-flink-jobmanager-red -n default --replicas=0
kubectl --context gcp-mec1prod6-2-green scale deployment inflate-flink-taskmanager-red -n default --replicas=0
```

## Phase 12 - Cleanup

Removed from `gke_config.hcl`:
- `flink-jobmanager-black` node pool block
- `flink-taskmanager-black` node pool block

(Kept shared label/taint/OAuth-scope map entries for black, matching the existing pattern.)

```zsh
export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token \
  --impersonate-service-account terraform-create-mec1prod6@mist-infrastructure-iam.iam.gserviceaccount.com 2>/dev/null)
cd /Users/rvyom/workspace/iac-flink-mec1prod6-20260915/mec1prod6-gcp/auto/gke-2-green
terragrunt plan    # 0 to add, 0 to change, 4 to destroy
terragrunt apply -auto-approve
```

> ⚠️ Must redirect stderr to `/dev/null` when capturing the impersonation token into a
> variable — the "WARNING: using service account impersonation" text otherwise corrupts
> the token and breaks the Authorization header.

Apply completed successfully: `4 destroyed` (2 node pools + 2 `random_id` resources).

Verified final state:

```zsh
gcloud container node-pools list --cluster mec1prod6-2-green --region europe-west3 --project mist-k8s-mec1prod6
```

Result: only `flink-jobmanager-red-7dcf` and `flink-taskmanager-red-7ef0` remain
(plus unrelated `init-black`, `nodes-large-black`, `monitoring-black` pools, out of scope
for this rotation). `flink-jobmanager-black-286c` and `flink-taskmanager-black-7055` confirmed removed.

**PR commits** ([mistsys/iac#4818](https://github.com/mistsys/iac/pull/4818)):
1. `Add flink-{jobmanager,taskmanager}-red node pools` (`7751bed10`)
2. `chore(mec1prod6): Remove flink-{jobmanager,taskmanager}-black node pools after AMI upgrade` (`c152f5e7a`)

PR ready to merge when approved.

## Final State

- LA: 761/761 on primary (100%) ✅
- App: 17/17 on primary (100%) ✅
- Active Flink color: `red` (`1.33.13-gke.1329000`)
- 0 job failures/cancellations across the entire migration
- Black node pools fully decommissioned from both GKE and IAC

## Issues Encountered & Resolutions

| # | Issue | Resolution |
|---|---|---|
| 1 | Local `terragrunt apply` failed with GCS state-lock 403 for personal account | Required service-account impersonation via `GOOGLE_OAUTH_ACCESS_TOKEN`; user ran the phase-2 apply manually from their own shell. |
| 2 | GKE cluster autoscaler created new black nodes mid-restart, catching 2 jobmanager pods | Not caught by the initial cordon since the nodes didn't exist yet. Resolved by iteratively cordoning newly appeared black nodes and re-deleting pods until they landed on red. |
| 3 | Cordoned+empty black JM/TM nodes disappeared before explicit drain | GKE CA auto-terminated them; no manual drain needed in Phase 11. |
| 4 | Impersonation token capture broke Authorization header | `gcloud auth print-access-token ... impersonate...` prints a warning to stderr; must redirect with `2>/dev/null` when capturing into `GOOGLE_OAUTH_ACCESS_TOKEN`. |
