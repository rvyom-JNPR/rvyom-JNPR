import boto3
import google.auth.transport.requests
from google.oauth2 import id_token

AUDIENCE = "111695966871158907715"
ROLE_ARN = "arn:aws:iam::125952381355:role/dataproc_mec2prod6_gcp_trust_role"
BUCKET   = "vyom-test-cross-provider"

print("=" * 50)
print("POSITIVE TEST — expect all steps to SUCCEED")
print("=" * 50)

# Step 1: Get OIDC token
auth_request = google.auth.transport.requests.Request()
token = id_token.fetch_id_token(auth_request, AUDIENCE)
print("\n[STEP 1] OIDC token fetch: SUCCESS ✓")
print("         Token preview:", token[:40], "...")

# Step 2: Assume AWS role via OIDC
sts = boto3.client("sts", region_name="us-west-2")
response = sts.assume_role_with_web_identity(
    RoleArn=ROLE_ARN,
    RoleSessionName="ctkb-positive-test",
    WebIdentityToken=token,
)
creds = response["Credentials"]
print("\n[STEP 2] sts:AssumeRoleWithWebIdentity: SUCCESS ✓")
print("         Assumed role:", response["AssumedRoleUser"]["Arn"])
print("         Expires at  :", creds["Expiration"])

# Step 3: List bucket
s3 = boto3.client(
    "s3",
    aws_access_key_id=creds["AccessKeyId"],
    aws_secret_access_key=creds["SecretAccessKey"],
    aws_session_token=creds["SessionToken"],
    region_name="us-west-2",
)
objects = s3.list_objects_v2(Bucket=BUCKET)
print("\n[STEP 3] s3:ListBucket: SUCCESS ✓")
for obj in objects.get("Contents", []):
    print("         -", obj["Key"], "(", obj["Size"], "bytes )")

# Step 4: Download file
s3.download_file(BUCKET, "test.txt", "/tmp/ctkb_result.txt")
content = open("/tmp/ctkb_result.txt").read().strip()
print("\n[STEP 4] s3:GetObject: SUCCESS ✓")
print("         File content:", content)

print("\n" + "=" * 50)
print("POSITIVE TEST COMPLETE — all steps succeeded ✓")
print("=" * 50)