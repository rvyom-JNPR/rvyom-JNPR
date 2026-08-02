import boto3
import google.auth.transport.requests
from google.oauth2 import id_token

AUDIENCE = "111695966871158907715"
ROLE_ARN = "arn:aws:iam::125952381355:role/dataproc_mec2prod6_gcp_trust_role"
BUCKET   = "vyom-test-cross-provider"

print("=" * 50)
print("NEGATIVE TEST — expect all steps to FAIL")
print("=" * 50)

# Step 1: Get OIDC token — this WILL succeed
try:
    auth_request = google.auth.transport.requests.Request()
    token = id_token.fetch_id_token(auth_request, AUDIENCE)
    print("\n[STEP 1] OIDC token fetch: SUCCESS (expected - SA can always get tokens)")
    print("         Token preview:", token[:40], "...")
except Exception as e:
    print("\n[STEP 1] OIDC token fetch: FAILED (unexpected):", e)
    exit(1)

# Step 2: Try to assume role — should FAIL
print("\n[STEP 2] Attempting sts:AssumeRoleWithWebIdentity ...")
try:
    sts = boto3.client("sts", region_name="us-west-2")
    creds = sts.assume_role_with_web_identity(
        RoleArn=ROLE_ARN,
        RoleSessionName="ctkb-negative-test",
        WebIdentityToken=token,
    )["Credentials"]
    print("         ERROR: Role assumed — this should NOT have worked!")
except Exception as e:
    print("         EXPECTED FAILURE:", type(e).__name__, "-", str(e)[:120])

# Step 3: Try direct S3 access — should FAIL
print("\n[STEP 3] Attempting S3 access without valid credentials ...")
try:
    s3 = boto3.client("s3", region_name="us-west-2")
    s3.download_file(BUCKET, "test.txt", "/tmp/test_negative.txt")
    print("         ERROR: S3 download succeeded — this should NOT have worked!")
except Exception as e:
    print("         EXPECTED FAILURE:", type(e).__name__, "-", str(e)[:120])

print("\n" + "=" * 50)
print("NEGATIVE TEST COMPLETE — all failures above are expected ✓")
print("=" * 50)