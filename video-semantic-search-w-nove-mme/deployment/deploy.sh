#!/bin/bash
set -e

# ============================================
# Video Search - Full Automated Deployment
# ============================================
# Usage: ./deploy.sh [profile] [region] [email]

PROFILE="${1:-mme-account}"
REGION="${2:-us-east-1}"
PROJECT="video-search-v2"

export AWS_PROFILE="$PROFILE"
export AWS_DEFAULT_REGION="$REGION"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TF_DIR="$ROOT_DIR/terraform"

echo "============================================"
echo "  Video Search - Automated Deployment"
echo "  Profile: $PROFILE | Region: $REGION"
echo "============================================"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REPO="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$PROJECT-worker"
echo "Account: $ACCOUNT_ID"

# ============================================
# Step 1: Package Lambda functions
# ============================================
echo ""
echo ">>> Step 1: Packaging Lambda functions..."

cd "$ROOT_DIR/lambda"

for func in search_function upload_function video_function entity_function project_function; do
  zip -j "$func.zip" "functions/$func.py" 2>/dev/null
  echo "  ✓ $func.zip"
done

for func in orchestrator_function embedding_function transcription_function merge_function celebrity_detection_function caption_function; do
  zip -j "$func.zip" "functions/$func.py" 2>/dev/null
  echo "  ✓ $func.zip"
done

# Package Lambda layer
echo "  Building shared layer..."
LAYER_DIR=$(mktemp -d)
python3 -m pip install opensearch-py requests-aws4auth -t "$LAYER_DIR/python/" --quiet 2>/dev/null
cp "$ROOT_DIR/lib/"*.py "$LAYER_DIR/python/" 2>/dev/null || true
cd "$LAYER_DIR"
zip -r "$ROOT_DIR/lambda/layer.zip" python/ -x "python/__pycache__/*" >/dev/null
rm -rf "$LAYER_DIR"
echo "  ✓ layer.zip"

# ============================================
# Step 2: Build and push Docker worker image
# ============================================
echo ""
echo ">>> Step 2: Building worker Docker image..."

cd "$ROOT_DIR"

cat > .env.lambda << EOF
AWS_REGION=$REGION
AWS_ACCOUNT_ID=$ACCOUNT_ID
S3_VIDEO_BUCKET=$PROJECT-videos-$ACCOUNT_ID
S3_VECTOR_BUCKET=$PROJECT-vectors-$ACCOUNT_ID
NOVA_MODEL_ID=amazon.nova-2-multimodal-embeddings-v1:0
VIDEOS_TABLE=$PROJECT-videos
SEGMENTS_TABLE=$PROJECT-segments
ENTITIES_TABLE=$PROJECT-entities
PROJECTS_TABLE=$PROJECT-projects
EOF

docker buildx build --platform linux/amd64 --provenance=false --sbom=false \
  -t "$PROJECT-worker:latest" -f deployment/Dockerfile . 2>&1 | tail -1

aws ecr describe-repositories --repository-names "$PROJECT-worker" --region "$REGION" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "$PROJECT-worker" --region "$REGION" --image-tag-mutability MUTABLE >/dev/null

cd "$TF_DIR"
terraform init -input=false >/dev/null 2>&1
terraform workspace select "$PROFILE" 2>/dev/null || terraform workspace new "$PROFILE"
terraform state show aws_ecr_repository.worker >/dev/null 2>&1 || \
  terraform import -var="aws_profile=$PROFILE" -var="aws_region=$REGION" aws_ecr_repository.worker "$PROJECT-worker" >/dev/null 2>&1 || true
cd "$ROOT_DIR"

aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com" 2>/dev/null

docker tag "$PROJECT-worker:latest" "$ECR_REPO:latest"
docker buildx build --platform linux/amd64 --provenance=false --sbom=false \
  -t "$ECR_REPO:latest" -f deployment/Dockerfile --push . 2>&1 | tail -1
echo "  ✓ Worker image pushed to ECR"

# ============================================
# Step 3: Terraform apply
# ============================================
echo ""
echo ">>> Step 3: Running Terraform..."

cd "$TF_DIR"
terraform init -input=false >/dev/null 2>&1
terraform workspace select "$PROFILE" 2>/dev/null || terraform workspace new "$PROFILE"

DEPLOY_ID=$(date +%s | tail -c 5)

terraform apply -auto-approve \
  -var="aws_profile=$PROFILE" \
  -var="aws_region=$REGION" \
  -var="deploy_id=$DEPLOY_ID" 2>&1 | tail -20

terraform apply -auto-approve \
  -var="aws_profile=$PROFILE" \
  -var="aws_region=$REGION" \
  -var="deploy_id=$DEPLOY_ID" 2>&1 | tail -5

CF_STATIC=$(terraform output -raw cloudfront_static_domain)
CF_VIDEO=$(terraform output -raw cloudfront_video_domain)
API_ENDPOINT=$(terraform output -raw api_endpoint)
COGNITO_CLIENT=$(terraform output -raw cognito_client_id)
COGNITO_DOMAIN=$(terraform output -raw cognito_domain)

echo "  ✓ Infrastructure deployed"

# ============================================
# Step 4: Update Lambda functions
# ============================================
echo ""
echo ">>> Step 4: Updating Lambda functions..."

cd "$ROOT_DIR/lambda"
LAYER_ARN=$(aws lambda publish-layer-version \
  --layer-name "$PROJECT-shared-layer" \
  --zip-file fileb://layer.zip \
  --compatible-runtimes python3.13 python3.11 \
  --region "$REGION" \
  --query 'LayerVersionArn' --output text)
echo "  ✓ Layer published: $LAYER_ARN"

for func in search_function upload_function video_function entity_function project_function; do
  aws lambda update-function-code \
    --function-name "$PROJECT-${func//_/-}" \
    --zip-file "fileb://$func.zip" \
    --region "$REGION" >/dev/null 2>&1
  aws lambda wait function-updated --function-name "$PROJECT-${func//_/-}" --region "$REGION" 2>/dev/null || true
  aws lambda update-function-configuration \
    --function-name "$PROJECT-${func//_/-}" \
    --layers "$LAYER_ARN" \
    --region "$REGION" >/dev/null 2>&1
  aws lambda wait function-updated --function-name "$PROJECT-${func//_/-}" --region "$REGION" 2>/dev/null || true
  echo "  ✓ $func"
done

echo "  Updating pipeline Lambda functions..."
for func in orchestrator_function embedding_function transcription_function merge_function; do
  FNAME="$PROJECT-${func//_function/}"
  FNAME="${FNAME//_/-}"
  aws lambda update-function-code \
    --function-name "$FNAME" \
    --zip-file "fileb://$func.zip" \
    --region "$REGION" >/dev/null 2>&1 || true
  aws lambda wait function-updated --function-name "$FNAME" --region "$REGION" 2>/dev/null || true
  case "$func" in embedding_function|transcription_function|merge_function)
    aws lambda update-function-configuration \
      --function-name "$FNAME" \
      --layers "$LAYER_ARN" \
      --region "$REGION" >/dev/null 2>&1 || true
    aws lambda wait function-updated --function-name "$FNAME" --region "$REGION" 2>/dev/null || true
  esac
  echo "  ✓ $func"
done

# Docker-based pipeline Lambda
for func in shot-segmentation celebrity-detection caption; do
  aws lambda update-function-code \
    --function-name "$PROJECT-$func" \
    --image-uri "$ECR_REPO:latest" \
    --region "$REGION" >/dev/null 2>&1 || true
  echo "  ✓ $func (Docker)"
done

# API Gateway CORS
API_ID=$(aws apigateway get-rest-apis --query "items[?name=='$PROJECT-api'].id" --output text --region "$REGION")
for TYPE in DEFAULT_4XX DEFAULT_5XX; do
  aws apigateway put-gateway-response --rest-api-id "$API_ID" --response-type "$TYPE" \
    --response-parameters '{"gatewayresponse.header.Access-Control-Allow-Origin":"'"'"'*'"'"'","gatewayresponse.header.Access-Control-Allow-Headers":"'"'"'Content-Type,Authorization'"'"'","gatewayresponse.header.Access-Control-Allow-Methods":"'"'"'GET,POST,PUT,DELETE,OPTIONS'"'"'"}' \
    --region "$REGION" > /dev/null 2>&1
done
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name prod --region "$REGION" > /dev/null 2>&1
echo "  ✓ API Gateway CORS configured"

# ============================================
# Step 5: Deploy frontend
# ============================================
echo ""
echo ">>> Step 5: Deploying frontend..."

STATIC_BUCKET="$PROJECT-static-$ACCOUNT_ID"

cat > "$ROOT_DIR/frontend-static/js/config.js" << EOF
const CONFIG = {
    API_ENDPOINT: '${API_ENDPOINT}/api',
    COGNITO_DOMAIN: '${COGNITO_DOMAIN}.auth.$REGION.amazoncognito.com',
    COGNITO_CLIENT_ID: '${COGNITO_CLIENT}',
    COGNITO_REDIRECT_URI: 'https://${CF_STATIC}',
    VIDEO_CDN: 'https://${CF_VIDEO}'
};
EOF

aws s3 sync "$ROOT_DIR/frontend-static/" "s3://$STATIC_BUCKET/" \
  --delete --exclude ".DS_Store" --region "$REGION" >/dev/null

CF_DIST_ID=$(aws cloudfront list-distributions --query "DistributionList.Items[?Origins.Items[?DomainName=='${STATIC_BUCKET}.s3.${REGION}.amazonaws.com']].Id" --output text)
if [ -n "$CF_DIST_ID" ]; then
  aws cloudfront create-invalidation --distribution-id "$CF_DIST_ID" --paths "/*" >/dev/null
fi
echo "  ✓ Frontend deployed"

# ============================================
# Step 6: Configure Cognito + S3 CORS
# ============================================
echo ""
echo ">>> Step 6: Configuring Cognito + S3..."

POOL_ID=$(aws cognito-idp list-user-pools --max-results 20 --query "UserPools[?contains(Name,'$PROJECT')].Id" --output text --region "$REGION")

aws cognito-idp update-user-pool-client \
  --user-pool-id "$POOL_ID" \
  --client-id "$COGNITO_CLIENT" \
  --callback-urls "https://$CF_STATIC" \
  --logout-urls "https://$CF_STATIC" \
  --allowed-o-auth-flows implicit \
  --allowed-o-auth-scopes email openid profile \
  --allowed-o-auth-flows-user-pool-client \
  --supported-identity-providers COGNITO \
  --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH ALLOW_USER_PASSWORD_AUTH \
  --region "$REGION" >/dev/null

aws s3api put-bucket-cors --bucket "$PROJECT-videos-$ACCOUNT_ID" --cors-configuration "{
  \"CORSRules\": [{
    \"AllowedHeaders\": [\"*\"],
    \"AllowedMethods\": [\"GET\", \"PUT\", \"POST\"],
    \"AllowedOrigins\": [\"https://$CF_STATIC\"],
    \"ExposeHeaders\": [\"ETag\"],
    \"MaxAgeSeconds\": 3600
  }]
}" --region "$REGION"

# Bedrock bucket policy
EXISTING_POLICY=$(aws s3api get-bucket-policy --bucket "$PROJECT-videos-$ACCOUNT_ID" --query Policy --output text --region "$REGION" 2>/dev/null || echo '{"Version":"2012-10-17","Statement":[]}')
HAS_BEDROCK=$(echo "$EXISTING_POLICY" | python3 -c "import sys,json; p=json.load(sys.stdin); print(any('bedrock' in str(s.get('Principal','')) for s in p.get('Statement',[])))" 2>/dev/null || echo "False")
if [ "$HAS_BEDROCK" = "False" ]; then
  CF_VIDEO_DIST=$(aws cloudfront list-distributions --query "DistributionList.Items[?Origins.Items[0].DomainName=='$PROJECT-videos-$ACCOUNT_ID.s3.$REGION.amazonaws.com'].Id" --output text --region "$REGION" 2>/dev/null)
  cat > /tmp/bucket-policy.json << BPEOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockAccess",
      "Effect": "Allow",
      "Principal": {"Service": "bedrock.amazonaws.com"},
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::$PROJECT-videos-$ACCOUNT_ID/*"
    },
    {
      "Sid": "CloudFrontAccess",
      "Effect": "Allow",
      "Principal": {"Service": "cloudfront.amazonaws.com"},
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::$PROJECT-videos-$ACCOUNT_ID/*",
      "Condition": {"StringEquals": {"AWS:SourceArn": "arn:aws:cloudfront::$ACCOUNT_ID:distribution/$CF_VIDEO_DIST"}}
    }
  ]
}
BPEOF
  aws s3api put-bucket-policy --bucket "$PROJECT-videos-$ACCOUNT_ID" --policy file:///tmp/bucket-policy.json --region "$REGION"
fi
echo "  ✓ Cognito + S3 configured"

# ============================================
# Step 7: Create test user (optional)
# ============================================
DEFAULT_USER="${3:-}"
if [ -n "$DEFAULT_USER" ]; then
  echo ""
  echo ">>> Step 7: Creating test user..."
  aws cognito-idp admin-create-user \
    --user-pool-id "$POOL_ID" \
    --username "$DEFAULT_USER" \
    --temporary-password "TempPass123!" \
    --user-attributes Name=email,Value="$DEFAULT_USER" Name=email_verified,Value=true \
    --region "$REGION" >/dev/null 2>&1 || true
  aws cognito-idp admin-set-user-password \
    --user-pool-id "$POOL_ID" \
    --username "$DEFAULT_USER" \
    --password "TempPass123!" \
    --permanent \
    --region "$REGION" >/dev/null 2>&1
  echo "  ✓ User created: $DEFAULT_USER"
fi

# ============================================
# Done!
# ============================================
echo ""
echo "============================================"
echo "  ✅ Deployment Complete!"
echo "============================================"
echo ""
echo "  App URL:  https://$CF_STATIC"
echo "  API:      ${API_ENDPOINT}/api"
echo "  Video CDN: https://$CF_VIDEO"
echo ""
