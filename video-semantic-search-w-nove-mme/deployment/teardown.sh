#!/bin/bash
set -e

# ============================================
# Video Search - Full Teardown
# ============================================
# Usage: ./teardown.sh [profile] [region]

PROFILE="${1:-mme-account}"
REGION="${2:-us-east-1}"
PROJECT="video-search-v2"

export AWS_PROFILE="$PROFILE"
export AWS_DEFAULT_REGION="$REGION"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TF_DIR="$(dirname "$SCRIPT_DIR")/terraform"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "⚠️  This will destroy ALL resources for $PROJECT in $ACCOUNT_ID ($REGION)"
read -p "Type 'yes' to confirm: " CONFIRM
[ "$CONFIRM" != "yes" ] && echo "Aborted." && exit 1

# ============================================
# Step 1: Empty S3 buckets (required before delete)
# ============================================
echo ""
echo ">>> Emptying S3 buckets..."
for BUCKET in "$PROJECT-videos-$ACCOUNT_ID" "$PROJECT-static-$ACCOUNT_ID"; do
  aws s3 rm "s3://$BUCKET" --recursive --region "$REGION" 2>/dev/null || true
  python3 -c "
import boto3
s3 = boto3.Session(profile_name='$PROFILE', region_name='$REGION').client('s3')
try:
    paginator = s3.get_paginator('list_object_versions')
    for page in paginator.paginate(Bucket='$BUCKET'):
        objects = [{'Key':v['Key'],'VersionId':v['VersionId']} for v in page.get('Versions',[])]
        objects += [{'Key':d['Key'],'VersionId':d['VersionId']} for d in page.get('DeleteMarkers',[])]
        if objects:
            s3.delete_objects(Bucket='$BUCKET', Delete={'Objects': objects})
except: pass
" 2>/dev/null || true
done

# ============================================
# Step 2: Delete S3 Vectors (not managed by Terraform)
# ============================================
echo ">>> Deleting S3 Vectors..."
python3 -c "
import boto3
s3v = boto3.Session(profile_name='$PROFILE', region_name='$REGION').client('s3vectors')
bucket = '$PROJECT-vectors-$ACCOUNT_ID'
try:
    indices = s3v.list_indexes(vectorBucketName=bucket).get('indexes', [])
    for idx in indices:
        s3v.delete_index(vectorBucketName=bucket, indexName=idx['indexName'])
        print(f'  Deleted index: {idx[\"indexName\"]}')
    s3v.delete_vector_bucket(vectorBucketName=bucket)
    print(f'  Deleted vector bucket: {bucket}')
except Exception as e:
    print(f'  {e}')
" 2>/dev/null || true

# ============================================
# Step 3: Delete ECR images (required before repo delete)
# ============================================
echo ">>> Deleting ECR images..."
# Delete all Rekognition Collections (per-project + legacy global)
python3 -c "
import boto3
rek = boto3.Session(profile_name='$PROFILE', region_name='$REGION').client('rekognition')
try:
    cols = rek.list_collections().get('CollectionIds', [])
    for c in cols:
        if c.startswith('$PROJECT'):
            rek.delete_collection(CollectionId=c)
            print(f'  Deleted collection: {c}')
except Exception as e:
    print(f'  {e}')
" 2>/dev/null || true
aws ecr batch-delete-image --repository-name "$PROJECT-worker" \
  --image-ids "$(aws ecr list-images --repository-name "$PROJECT-worker" --query 'imageIds[*]' --output json --region "$REGION" 2>/dev/null)" \
  --region "$REGION" 2>/dev/null || true

# ============================================
# Step 4: Terraform destroy
# ============================================
echo ">>> Running Terraform destroy..."
cd "$TF_DIR"
terraform init -input=false >/dev/null 2>&1
terraform workspace select "$PROFILE" 2>/dev/null || terraform workspace new "$PROFILE"
terraform destroy -auto-approve \
  -var="aws_profile=$PROFILE" \
  -var="aws_region=$REGION" 2>&1 | tail -10

# ============================================
# Step 5: Clean up any remaining resources
# ============================================
echo ">>> Cleaning up remaining resources..."
# Delete any Lambda functions not destroyed by Terraform
for FUNC in $(aws lambda list-functions --query "Functions[?starts_with(FunctionName,'$PROJECT-')].FunctionName" --output text --region "$REGION" 2>/dev/null); do
  aws lambda delete-function --function-name "$FUNC" --region "$REGION" 2>/dev/null
  echo "  Deleted Lambda: $FUNC"
done
# Delete any remaining log groups
for LG in $(aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/$PROJECT-" --query "logGroups[].logGroupName" --output text --region "$REGION" 2>/dev/null); do
  aws logs delete-log-group --log-group-name "$LG" --region "$REGION" 2>/dev/null
done
for LG in $(aws logs describe-log-groups --log-group-name-prefix "/aws/states/$PROJECT-" --query "logGroups[].logGroupName" --output text --region "$REGION" 2>/dev/null); do
  aws logs delete-log-group --log-group-name "$LG" --region "$REGION" 2>/dev/null
done
# Delete any remaining DynamoDB tables
for TBL in $(aws dynamodb list-tables --query "TableNames[?starts_with(@,'$PROJECT-')]" --output text --region "$REGION" 2>/dev/null); do
  aws dynamodb delete-table --table-name "$TBL" --region "$REGION" 2>/dev/null
  echo "  Deleted table: $TBL"
done

echo ""
echo "✅ Teardown complete."
