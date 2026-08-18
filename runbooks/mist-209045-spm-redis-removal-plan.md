# MIST-209045: Remove GCP Managed Memorystore (and AWS ElastiCache) for SPM — Removal Plan

## Background

- **MIST-53101** (Dec 2021, Xuhui Hu / Vlad Kozin): SPM requested a small dedicated
  non-clustered Redis in staging, deployed **manually** (not via `landcode`/`iac` Terraform
  code) in both `aws-staging` and `gcp-staging`.
- **Jan 25, 2023** — Vlad Kozin commented on MIST-53101: *"Please keep this instance
  running, I use it for development from time to time."*
- **MIST-209045** (current ticket) — Slack thread: "do we still need redis of spm in gcp
  staging?" → Scott Bowers: *"SPM no longer uses Redis. Feel free to remove it from
  staging envs. ... Redis usage for SPM ended back in 2022."*
- **Conflict to resolve before deleting:** Scott's sign-off is more recent than Vlad's
  request, but Vlad's request was never explicitly withdrawn. Recommend a quick
  confirmation ping to Vlad/Scott before executing deletions, given this is a one-way
  door (data loss on delete).

## Current State — Discovered Resources

### 1. GCP staging (project `mist-host-staging`)

| Resource | Details |
|---|---|
| Memorystore (Redis) instance | `spm-staging`, region `us-west2`, tier `STANDARD_HA`, 2 GiB, host `10.6.0.164`, reservedIpRange `10.6.0.160/29`, created 2021-11-20, labels `role=spm, type=redis` |
| Private DNS record | `redis-spm-staging.mist.pvt.` (A) → `10.6.0.164`, zone `mist-staging-private` |
| Firewall rules | None dedicated to spm found |
| Reserved standalone IP address | None found (IP owned directly by the Memorystore instance) |

### 2. AWS staging (account `660610034966`, region `us-east-1`)

| Resource | Details |
|---|---|
| ElastiCache Replication Group | `spm` (description: "spm of MIST-53101"), `cache.t3.medium`, engine redis 7.1.0, created 2021-11-19 |
| Member clusters | `spm-001` (primary, us-east-1c), `spm-002` (replica, us-east-1e) — deleted automatically when the replication group is deleted |
| Route53 CNAME record | `redis-spm-staging.mist.pvt.` → `spm-ro.vtgldn.ng.0001.use1.cache.amazonaws.com`, zone `Z3B74ZK9O9M0G` (`mist.pvt.`) |
| Security group | `sg-9f805bee` ("staging_redis_cluster") — **shared** with other Redis instances; do **not** delete |

### 3. IaC repo state

- No Terraform/terragrunt code manages `spm` in `iac` or `landcode` today (it was always
  deployed by hand; `landcode`'s copy was already removed in an earlier unrelated
  cleanup, MIST-194972).
- Only trace left in code: a stale comment `10.6.0.160 #taken by spm` in
  `gcp-staging/auto/redis-noncluster/README.md` in `iac`.
- **PR already opened:** https://github.com/mistsys/iac/pull/4481 — removes that stale
  comment.

## Removal Plan

### Step 0 — Sign-off (recommended before any deletion)
- [ ] Confirm with Vlad Kozin / Scott Bowers that the Jan 2023 "keep it running for dev"
      request no longer applies (e.g., post a comment on MIST-53101/MIST-209045 with a
      grace period, or get explicit thumbs-up).

### Step 1 — GCP staging

```bash
# Verify before deleting
gcloud redis instances describe spm-staging \
  --region=us-west2 --project=mist-host-staging

# Delete DNS record
gcloud dns record-sets delete redis-spm-staging.mist.pvt. --type=A \
  --zone=mist-staging-private --project=mist-host-staging

# Delete Memorystore instance
gcloud redis instances delete spm-staging \
  --region=us-west2 --project=mist-host-staging --quiet

# Verify removal
gcloud redis instances list --region=us-west2 --project=mist-host-staging --filter="name~spm"
gcloud dns record-sets list --zone=mist-staging-private --project=mist-host-staging --filter="name~spm"
```

### Step 2 — AWS staging

```bash
# Verify before deleting
aws-okta exec mist-staging-admin -- aws elasticache describe-replication-groups \
  --replication-group-id spm

# Delete Route53 CNAME record
aws-okta exec mist-staging-admin -- aws route53 change-resource-record-sets \
  --hosted-zone-id Z3B74ZK9O9M0G \
  --change-batch '{
    "Changes": [{
      "Action": "DELETE",
      "ResourceRecordSet": {
        "Name": "redis-spm-staging.mist.pvt.",
        "Type": "CNAME",
        "TTL": 300,
        "ResourceRecords": [{"Value": "spm-ro.vtgldn.ng.0001.use1.cache.amazonaws.com"}]
      }
    }]
  }'

# Delete the ElastiCache replication group (also removes spm-001 & spm-002)
aws-okta exec mist-staging-admin -- aws elasticache delete-replication-group \
  --replication-group-id spm \
  --no-retain-primary-cluster

# Verify removal (may take a few minutes to fully delete)
aws-okta exec mist-viewer -- aws elasticache describe-replication-groups \
  --query "ReplicationGroups[?ReplicationGroupId=='spm']"
```

> Note: Do **not** touch security group `sg-9f805bee` (`staging_redis_cluster`) — it is
> shared by other Redis instances in staging.

### Step 3 — IaC repo cleanup
- [x] PR #4481 opened: removes stale `#taken by spm` comment in
      `gcp-staging/auto/redis-noncluster/README.md`.
- [ ] Merge PR #4481.

### Step 4 — Close the loop
- [ ] Comment on MIST-209045 / MIST-53101 confirming both instances were removed, link
      the PR, and close the tickets.

## Rollback / Safety Notes
- Both instances have `SnapshotRetentionLimit: 0` (AWS) / `persistenceMode: DISABLED`
  (GCP) — **no automatic backups exist**. If anyone still depends on data in these
  instances, it will be unrecoverable after deletion.
- Recommend taking a manual final snapshot/backup only if there's any doubt about
  current usage (unlikely needed given confirmed disuse since 2022, but cheap
  insurance).
