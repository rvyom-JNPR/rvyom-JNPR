#!/usr/bin/env bash
# Verifies that the PMA Spark stat Snowflake credentials/key secrets in all
# 12 production environments + aws-staging + gcp-staging exactly match
# (byte-for-byte) the global source-of-truth secrets they were copied from
# (MIST-206719). See plan/pma_snowflake_secrets_rollout.txt PHASE 8 for
# background, caveats, and known-good results.
#
# Requires: aws-okta CLI with the "secrets-writer" profile (global account)
# plus each env's admin profile, and gcloud with permission to impersonate
# each env's terraform-create-<env> service account. No VPN required.
#
# Run from the iac repo root (any branch):
#   ./plan/pma_snowflake_secrets_verify.sh   (if copied into iac repo), or
#   bash pma_snowflake_secrets_verify.sh      (from within iac repo root)

set -uo pipefail
REPO_ROOT=$(git rev-parse --show-toplevel)
TMPD=$(mktemp -d); trap 'rm -rf "$TMPD"' EXIT

# Fetch the 4 global source-of-truth values once, byte-exact (secrets-writer profile, global account, us-east-1)
eval "$(aws-okta env secrets-writer)" >/dev/null 2>&1
fetch_global() {
  local name="$1" dest="$2"
  aws secretsmanager get-secret-value --secret-id "$name" --region us-east-1 --output json | jq -rj '.SecretString' > "$dest"
}
fetch_global mist/global/airflow2/pma_stat_snowflake_credentials_production "$TMPD/global_cred_prod"
fetch_global mist/global/airflow2/pma_snowflake_key_production               "$TMPD/global_key_prod"
fetch_global mist/global/airflow2/pma_stat_snowflake_credentials_staging     "$TMPD/global_cred_staging"
fetch_global mist/global/airflow2/pma_snowflake_key_staging                  "$TMPD/global_key_staging"

compare_files() {
  local env="$1" secret="$2" got="$3" expected="$4"
  if [ ! -s "$got" ]; then
    echo "FAIL      $env  $secret  -> not found / empty"
  elif cmp -s "$got" "$expected"; then
    echo "OK        $env  $secret  -> matches global"
  else
    echo "MISMATCH  $env  $secret  -> does NOT match global"
  fi
}

check_aws() {
  local env="$1" profile="$2" region="$3" path1="$4" path2="$5" exp1="$6" exp2="$7"
  eval "$(aws-okta env "$profile")" >/dev/null 2>&1
  aws secretsmanager get-secret-value --secret-id "$path1" --region "$region" --output json 2>/dev/null | jq -rj '.SecretString' > "$TMPD/v1" || : > "$TMPD/v1"
  aws secretsmanager get-secret-value --secret-id "$path2" --region "$region" --output json 2>/dev/null | jq -rj '.SecretString' > "$TMPD/v2" || : > "$TMPD/v2"
  compare_files "$env" "$path1" "$TMPD/v1" "$exp1"
  compare_files "$env" "$path2" "$TMPD/v2" "$exp2"
}

check_gcp() {
  local env="$1" sa="$2" secret1="$3" secret2="$4" exp1="$5" exp2="$6"
  export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token --impersonate-service-account "$sa" 2>/dev/null)
  local project
  project=$(cd "${REPO_ROOT}/${env}/boot/projects" && terragrunt output -json service_projects 2>/dev/null | jq -r '.dataproc')
  gcloud secrets versions access latest --secret="$secret1" --project="$project" --impersonate-service-account="$sa" > "$TMPD/v1" 2>/dev/null || : > "$TMPD/v1"
  gcloud secrets versions access latest --secret="$secret2" --project="$project" --impersonate-service-account="$sa" > "$TMPD/v2" 2>/dev/null || : > "$TMPD/v2"
  compare_files "$env ($project)" "$secret1" "$TMPD/v1" "$exp1"
  compare_files "$env ($project)" "$secret2" "$TMPD/v2" "$exp2"
}

check_aws aws-production mist-admin  us-west-1      mist/production/airflow2/pma_stat_snowflake_credentials_production mist/production/airflow2/pma_snowflake_key_production "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_aws use1prod2-aws  kalam-admin us-east-1      mist/use1prod2/airflow2/pma_stat_snowflake_credentials_production  mist/use1prod2/airflow2/pma_snowflake_key_production  "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_aws apse2prod5-aws aus-admin   ap-southeast-2 mist/apse2prod5/airflow2/pma_stat_snowflake_credentials_production mist/apse2prod5/airflow2/pma_snowflake_key_production "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_aws eu-aws         eu-admin    eu-central-1   mist/eu/airflow2/pma_stat_snowflake_credentials_production         mist/eu/airflow2/pma_snowflake_key_production         "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_aws aws-staging    mist-admin  us-east-1      mist/staging/airflow2/pma_stat_snowflake_credentials_staging       mist/staging/airflow2/pma_snowflake_key_staging       "$TMPD/global_cred_staging" "$TMPD/global_key_staging"

check_gcp gcp-production terraform-create-prod@mist-infrastructure-iam.iam.gserviceaccount.com        pma_stat_snowflake_credentials_production pma_snowflake_key_production "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_gcp asne1prod7-gcp terraform-create-asne1prod7@mist-infrastructure-iam.iam.gserviceaccount.com   pma_stat_snowflake_credentials_production pma_snowflake_key_production "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_gcp aso1prod5-gcp  terraform-create-aso1prod5@mist-infrastructure-iam.iam.gserviceaccount.com    pma_stat_snowflake_credentials_production pma_snowflake_key_production "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_gcp euwe1prod3-gcp terraform-create-euwe1prod3@mist-infrastructure-iam.iam.gserviceaccount.com   pma_stat_snowflake_credentials_production pma_snowflake_key_production "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_gcp mec1prod6-gcp  terraform-create-mec1prod6@mist-infrastructure-iam.iam.gserviceaccount.com    pma_stat_snowflake_credentials_production pma_snowflake_key_production "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_gcp mec2prod6-gcp  terraform-create-mec2prod6@mist-infrastructure-iam.iam.gserviceaccount.com    pma_stat_snowflake_credentials_production pma_snowflake_key_production "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_gcp usc1prod4-gcp  terraform-create-usc1prod4@mist-infrastructure-iam.iam.gserviceaccount.com    pma_stat_snowflake_credentials_production pma_snowflake_key_production "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_gcp nane1prod1-gcp terraform-create-nane1prod1@mist-infrastructure-iam.iam.gserviceaccount.com    pma_stat_snowflake_credentials_production pma_snowflake_key_production "$TMPD/global_cred_prod" "$TMPD/global_key_prod"
check_gcp gcp-staging    terraform-create-staging@mist-infrastructure-iam.iam.gserviceaccount.com      pma_stat_snowflake_credentials_staging    pma_snowflake_key_staging    "$TMPD/global_cred_staging" "$TMPD/global_key_staging"
