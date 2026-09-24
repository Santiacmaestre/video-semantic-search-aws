# Deployment Runbook — Hybrid Video Search with Nova MME

Command-by-command guide to create and destroy the demo infrastructure. Everything deploys to
**us-east-1** (hardcoded in `cdk/app.py`) under the stack name **`video-search-v2-stack`**.

All commands are run from the `video-semantic-search-w-nove-mme/` directory unless noted.

- [Part 0 — One-Time Setup](#part-0--one-time-setup)
- [Part 1 — Deploy](#part-1--deploy)
- [Part 2 — Verify](#part-2--verify)
- [Part 3 — Optional Extras](#part-3--optional-extras)
- [Part 4 — Destroy](#part-4--destroy)
- [Troubleshooting](#troubleshooting)

The whole stack — including the S3 Vectors bucket — is CloudFormation-managed. There are no manual
pre-deploy or post-destroy resource steps other than the two resources CDK deliberately retains
(step 4.3).

---

## Part 0 — One-Time Setup

### 0.1 Set your shell variables

Re-export these in every new terminal you use for this runbook.

```bash
export AWS_PROFILE=<your-profile>          # e.g. my-sandbox-account
export AWS_REGION=us-east-1
export STACK_NAME=video-search-v2-stack
export PROJECT_NAME=video-search-v2

# Confirm which account you're pointing at BEFORE deploying
aws sts get-caller-identity --region us-east-1

export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region us-east-1)
export VECTOR_BUCKET="${PROJECT_NAME}-vectors-${ACCOUNT_ID}"
echo "Account: $ACCOUNT_ID | Vector bucket: $VECTOR_BUCKET"
```

> `AWS_DEFAULT_REGION` is overridden by the profile's config, so every AWS CLI call below passes
> `--region us-east-1` explicitly.

### 0.2 Check required tooling

```bash
aws --version        # need AWS CLI v2
node --version       # need >= 18
python3 --version    # need >= 3.11
docker --version
cdk --version        # if "command not found", see 0.3
```

### 0.3 Install the CDK CLI (if missing)

```bash
npm install -g aws-cdk
cdk --version
```

### 0.4 Start Docker

Docker **must be running** for the whole deploy — CDK builds the Fargate ffmpeg image and bundles
the Lambda layer, the bootstrap function, and the vector-bucket cleanup function inside containers.

```bash
open -a Docker              # macOS Docker Desktop
docker info --format '{{.ServerVersion}}'   # must print a version, not an error
```

### 0.5 Enable Bedrock model access

Two models, both Amazon Nova (see the rationale in `cdk/config.py`):

| Model | ID | Used for |
|---|---|---|
| Nova 2 Multimodal Embeddings | `amazon.nova-2-multimodal-embeddings-v1:0` | video / audio / text embeddings |
| Nova 2 Lite | `us.amazon.nova-2-lite-v1:0` | segment captions, genre, query weight analysis |

Enable both in the Bedrock console (us-east-1) → **Model access**.

Two region constraints worth knowing before you start:

- **Nova MME is offered in us-east-1 only**, which is what pins the stack's region.
- **Nova 2 Lite is inference-profile-only**, reached through the `us.` prefix. One request fans out
  across us-east-1, us-east-2 and us-west-2, authorizing against whichever region it lands in — so
  all three must be permitted by any SCP/region restriction, or you get an intermittent
  `AccessDeniedException` naming a region you never called. `global.*` profiles are **not** usable
  here; they can route outside those three.

Verify both with real calls:

```bash
# Nova MME — confirm it's available in this account/region
aws bedrock list-foundation-models --region us-east-1 \
  --query "modelSummaries[?contains(modelId,'multimodal-embeddings')].modelId" --output table

# Nova 2 Lite — an AccessDeniedException here means access isn't enabled yet
aws bedrock-runtime converse \
  --region us-east-1 \
  --model-id us.amazon.nova-2-lite-v1:0 \
  --messages '[{"role":"user","content":[{"text":"ping"}]}]' \
  --query 'output.message.content[0].text' --output text
```

Run the Nova 2 Lite call a few times — the cross-region fan-out means a region restriction can
surface on some attempts and not others.

### 0.6 Bootstrap CDK in the account/region

Safe to re-run; it's a no-op if already bootstrapped.

```bash
cdk bootstrap "aws://${ACCOUNT_ID}/us-east-1"
```

---

## Part 1 — Deploy

### 1.1 Create the Python environment for CDK

```bash
cd cdk
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

The S3 Vectors construct uses `aws_cdk.aws_s3vectors`, which only exists in recent `aws-cdk-lib`
releases. If synth fails on that import, upgrade:

```bash
pip install --upgrade aws-cdk-lib
```

### 1.2 Synthesize and review

```bash
cdk synth > /dev/null        # fails fast on Python/CDK/cdk-nag errors
cdk diff                     # empty account => shows the full stack as new resources
```

### 1.3 Deploy

Takes roughly **25–40 minutes** on a first deploy (the OpenSearch `or1.medium.search` domain is the
long pole), plus the Docker image build.

```bash
cdk deploy --require-approval any-change
```

Unattended variant (skips the IAM confirmation prompt):

```bash
cdk deploy --require-approval never
```

Optional — override the query weight analyzer (e.g. a distilled model from the sibling sub-project)
instead of the Nova 2 Lite default:

```bash
cdk deploy -c nova_analyzer_model_id=arn:aws:bedrock:us-east-1:<ACCOUNT_ID>:provisioned-model/<ID>
```

CDK creates everything in one pass: the S3 Vectors bucket, OpenSearch, Step Functions, Lambda
functions, ECS Fargate cluster, API Gateway, CloudFront, Cognito, DynamoDB, SQS — then runs the
bootstrap custom resource (which depends on the vector bucket) to seed the demo user and sample
video.

### 1.4 Capture the stack outputs

```bash
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region us-east-1 \
  --query "Stacks[0].Outputs[].{Key:OutputKey,Value:OutputValue}" --output table
```

Key ones: `AppUrl`, `DemoUserEmail` (`demo@workshop.com`), `DemoUserPassword` (`Demo1234!`),
`CognitoUserPoolId`, `OpenSearchEndpoint`, `StateMachineArn`, `VectorBucketName`, `VectorBucketArn`.

```bash
export APP_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='AppUrl'].OutputValue" --output text)
export SM_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='StateMachineArn'].OutputValue" --output text)
export POOL_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue" --output text)
echo "$APP_URL"
```

---

## Part 2 — Verify

Deploy auto-bootstraps a demo user (`demo@workshop.com` / `Demo1234!`), a "Meridian Demo" project,
and kicks off ingestion of the Netflix Open Content *Meridian* short film. The pipeline runs
asynchronously after the stack completes.

### 2.1 Confirm the vector bucket and its per-project indexes

The bucket comes from the stack; the four indexes per project (visual, audio, transcription, entity)
are created at runtime by `vector_store.create_project_indices()`.

```bash
aws s3vectors get-vector-bucket --vector-bucket-name "$VECTOR_BUCKET" --region us-east-1

aws s3vectors list-indexes --vector-bucket-name "$VECTOR_BUCKET" --region us-east-1 \
  --query "indexes[].indexName" --output table
```

### 2.2 Watch the ingestion pipeline

```bash
# Latest Step Functions executions
aws stepfunctions list-executions --state-machine-arn "$SM_ARN" \
  --max-results 5 --region us-east-1 \
  --query "executions[].{name:name,status:status,start:startDate}" --output table

# Video status: pending -> processing -> completed
aws dynamodb scan --table-name "${PROJECT_NAME}-videos" --region us-east-1 \
  --query "Items[].{video_id:video_id.S,status:status.S}" --output table
```

### 2.3 Tail logs if something looks stuck

```bash
# Log group names carry hash suffixes — discover them first
aws logs describe-log-groups --region us-east-1 \
  --log-group-name-prefix "/aws/lambda/${PROJECT_NAME}" \
  --query "logGroups[].logGroupName" --output table

aws logs tail "<log-group-name>" --since 30m --region us-east-1
```

### 2.4 Open the app

```bash
open "$APP_URL"
```

Log in with `demo@workshop.com` / `Demo1234!`, wait for the Meridian video to show `completed`, then
try: `Meridian title page appears`, `Scott driving seeing a woman in the rearview mirror`,
`Someone calling captain foster`, `Thunder storm in the background`.

---

## Part 3 — Optional Extras

### 3.1 Add another user

The app uses the Cognito `USER_AUTH` flow, which rejects accounts stuck in
`FORCE_CHANGE_PASSWORD` — always set a permanent password right after creating the user.

```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$POOL_ID" \
  --username user@example.com \
  --temporary-password 'TempPass@123' \
  --user-attributes Name=email,Value=user@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region us-east-1

aws cognito-idp admin-set-user-password \
  --user-pool-id "$POOL_ID" \
  --username user@example.com \
  --password 'YourPassword1!' \
  --permanent \
  --region us-east-1
```

Password policy: min 8 chars with uppercase, lowercase, digit, and symbol.

### 3.2 Redeploy after code changes

```bash
cd cdk && source .venv/bin/activate
cdk deploy
```

Frontend changes need nothing extra — `cdk deploy` regenerates `frontend-static/js/config.js`,
uploads to S3, and invalidates the CloudFront cache. Never edit `config.js` by hand.

---

## Part 4 — Destroy

### 4.1 Destroy the CDK stack

```bash
cd cdk && source .venv/bin/activate
cdk destroy
```

This takes the S3 Vectors bucket with it. S3 Vectors refuses `DeleteVectorBucket` while any index
remains, so a cleanup custom resource (`vector_bucket.py` → `vector_bucket_function.py`) deletes
every index first — the S3 Vectors equivalent of `auto_delete_objects`. The vectors are derived data
and are rebuilt by re-running ingestion.

If it fails on the access-logs bucket (it often does — log objects land there during teardown),
empty that bucket and delete the stack directly:

```bash
# Find the access logs bucket (name appears in the failure message, or:)
aws s3api list-buckets --query "Buckets[?contains(Name,'accesslogsbucket')].Name" --output table

aws s3 rm "s3://<ACCESS_LOGS_BUCKET>" --recursive --region us-east-1
aws cloudformation delete-stack --stack-name "$STACK_NAME" --region us-east-1
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region us-east-1
```

### 4.2 Delete the retained resources

CDK retains the OpenSearch domain and the Cognito user pool so a failed destroy can't wipe data.
Delete them explicitly — **the OpenSearch domain is the main ongoing cost, so don't skip this.**

```bash
# OpenSearch domain (deletion takes 20-30 min)
aws opensearch list-domain-names --region us-east-1 --output table
aws opensearch delete-domain --domain-name <DOMAIN_NAME> --region us-east-1

# Cognito user pool
aws cognito-idp list-user-pools --max-results 20 --region us-east-1 --output table
aws cognito-idp delete-user-pool --user-pool-id <POOL_ID> --region us-east-1
```

### 4.3 Confirm nothing costly is left

```bash
aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region us-east-1 2>&1 | tail -2
# expect: "Stack with id video-search-v2-stack does not exist"

aws opensearch list-domain-names --region us-east-1
aws ecs list-clusters --region us-east-1
aws s3vectors list-vector-buckets --region us-east-1 --query "vectorBuckets[].vectorBucketName" --output table
aws s3api list-buckets --query "Buckets[?contains(Name,'video-search')].Name" --output table
```

If the vector bucket survived (the cleanup resource failed, or the stack was force-deleted), remove
it by hand:

```bash
for IDX in $(aws s3vectors list-indexes --vector-bucket-name "$VECTOR_BUCKET" --region us-east-1 \
              --query "indexes[].indexName" --output text); do
  aws s3vectors delete-index --vector-bucket-name "$VECTOR_BUCKET" --index-name "$IDX" --region us-east-1
done
aws s3vectors delete-vector-bucket --vector-bucket-name "$VECTOR_BUCKET" --region us-east-1
```

Leftovers that are cheap but you may want to clean by hand: the ECR repository holding the Fargate
image asset, `/aws/lambda/video-search-v2*` log groups, and the shared `cdk-hnb659fds-*` bootstrap
bucket (keep that one if you use CDK for anything else in this account).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `cdk: command not found` | `npm install -g aws-cdk` (step 0.3) |
| `Cannot connect to the Docker daemon` | Docker isn't running — `open -a Docker`, then retry (step 0.4) |
| `ModuleNotFoundError: aws_cdk.aws_s3vectors` | `aws-cdk-lib` too old for the S3 Vectors L1 — `pip install --upgrade aws-cdk-lib` |
| `cannot import name 'NagSuppressions' from 'cdk_nag'` | cdk-nag 3.x dropped those exports; `requirements.txt` caps it `<3` — reinstall with `pip install -r requirements.txt` |
| `AccessDeniedException` on Nova 2 Lite, intermittent | The `us.` inference profile routes across us-east-1/us-east-2/us-west-2; all three must be allowed (step 0.5) |
| `AccessDeniedException` on Nova MME | Model access not enabled, or you're deploying outside us-east-1 where MME isn't offered |
| Deploy fails creating the vector bucket with "already exists" | A bucket named `video-search-v2-vectors-<ACCOUNT_ID>` is left over from a pre-CDK deploy — delete it (step 4.3) or import it, since the name is fixed |
| `This stack uses assets, so the toolkit stack must be deployed` | Run `cdk bootstrap` (step 0.6) |
| Video stuck in `processing` forever | `ShotSegmentation` has retries but no catch handler — check the Step Functions execution and the Fargate task logs, then re-upload |
| Docker build fails on Apple Silicon | The task definition pins `LINUX_AMD64`; the build needs emulation — make sure Rosetta/buildx support is enabled in Docker Desktop |
| Destroy fails: bucket not empty | Empty the access-logs bucket, then delete the stack (step 4.1) |
