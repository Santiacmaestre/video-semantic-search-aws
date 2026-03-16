# Migration Analysis: Terraform to CDK

**Date:** 2026-03-12
**Scope:** Full comparison of ingestion pipeline, API Lambdas, shared libraries, and infrastructure configuration

## Executive Summary

All 18 Python source files (12 Lambda functions + 6 shared lib modules) are **functionally identical** between the Terraform and CDK deployments, with one minor non-impactful difference in `lib/dynamodb_store.py`. The search result differences are caused by **infrastructure configuration gaps** in the CDK deployment — specifically missing IAM permissions and a model ID discrepancy.

---

## Application Code Comparison

### Lambda Functions (all identical)

| File | Status |
|------|--------|
| `lambda/functions/orchestrator_function.py` | Identical |
| `lambda/functions/shot_segmentation_function.py` | Identical |
| `lambda/functions/embedding_function.py` | Identical |
| `lambda/functions/transcription_function.py` | Identical |
| `lambda/functions/celebrity_detection_function.py` | Identical |
| `lambda/functions/caption_function.py` | Identical |
| `lambda/functions/merge_function.py` | Identical |
| `lambda/functions/search_function.py` | Identical |
| `lambda/functions/upload_function.py` | Identical |
| `lambda/functions/video_function.py` | Identical |
| `lambda/functions/project_function.py` | Identical |
| `lambda/functions/entity_function.py` | Identical |

### Shared Library Modules

| File | Status | Notes |
|------|--------|-------|
| `lib/nova_embeddings.py` | Identical | |
| `lib/opensearch_client.py` | Identical | |
| `lib/vector_store.py` | Identical | |
| `lib/search_engine.py` | Identical | |
| `lib/prompt_analyzer.py` | Identical | |
| `lib/dynamodb_store.py` | Minor diff | CDK version reads table names from env vars with hardcoded fallbacks; Terraform version hardcodes names directly. **No runtime impact** since CDK tables use the same default names (`video-search-v2-*`). |

---

## Infrastructure Differences (Root Causes)

### ISSUE 0 — Missing Nova Lite Model in Bedrock IAM Policy (CRITICAL)

**CDK (`compute.py:101-108`):** Bedrock permissions only scope to two models:
- `amazon.nova-2-multimodal-embeddings-v1:0` (embeddings)
- `global.anthropic.claude-haiku-4-5-20251001-v1:0` (weight analysis)

**Terraform (`iam.tf:86,90`):** Grants `bedrock:InvokeModel` on `Resource: "*"` (all models).

**The caption Lambda (`caption_function.py:14`) uses `amazon.nova-lite-v1:0`**, which is NOT in the CDK IAM policy.

**Impact — THIS IS THE PRIMARY ROOT CAUSE of the reported issues:**
- Every `bedrock.invoke_model(modelId='amazon.nova-lite-v1:0')` call throws `AccessDeniedException`
- The exception is caught silently (`caption_function.py:89-91`), returning empty string captions
- Genre classification also fails silently (`caption_function.py:109-110`), returning `"Other"`
- Empty captions mean no BM25 text content for search, drastically reducing search scores
- This matches the exact symptoms reported: **low scores (0.300), genre = "Other", no captions**

**Fix:** Add Nova Lite model ARN to the Bedrock IAM policy in `compute.py`:
```python
resources=[
    f"arn:aws:bedrock:us-east-1::foundation-model/{NOVA_MODEL_ID}",
    f"arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0",  # captions + genre
    f"arn:aws:bedrock:us-east-1::foundation-model/{CLAUDE_MODEL_ID}",
    ...
],
```

Also add `NOVA_LITE_MODEL_ID = "amazon.nova-lite-v1:0"` to `config.py` for consistency.

**After fixing:** All previously ingested videos must be **re-ingested** (or at minimum, the caption step re-run) to generate proper captions and genre classifications.

---

### ISSUE 1 — Missing Rekognition Face Permissions (HIGH)

**CDK (`compute.py:116-123`):** Only grants `StartCelebrityRecognition` and `GetCelebrityRecognition`.

**Terraform (`iam.tf:96-104`):** Grants 9 Rekognition actions including `CreateCollection`, `IndexFaces`, `SearchFacesByImage`, `DeleteFaces`, `DeleteCollection`, `ListFaces`, `DetectFaces`.

**Impact:** The entity management system and project creation/deletion use Rekognition face collections. Without these permissions:
- Creating entities with face images will fail (face collection operations denied)
- The `@entity_name` visual re-ranking in search relies on entity face embeddings
- Project deletion cannot clean up associated face collections

**Where used:** `entity_function.py` and `project_function.py` use boto3 Rekognition client calls for face collection CRUD. These calls will throw `AccessDeniedException` under CDK.

**Fix:** Add missing Rekognition actions to `compute.py:117-121`:
```python
# Rekognition (celebrity detection + face collections for entities)
self.lambda_role.add_to_policy(iam.PolicyStatement(
    actions=[
        "rekognition:StartCelebrityRecognition",
        "rekognition:GetCelebrityRecognition",
        "rekognition:CreateCollection",
        "rekognition:DeleteCollection",
        "rekognition:IndexFaces",
        "rekognition:SearchFacesByImage",
        "rekognition:DeleteFaces",
        "rekognition:ListFaces",
        "rekognition:DetectFaces",
    ],
    resources=["*"],
))
```

---

### ISSUE 2 — Terraform Missing `bedrock:Converse` IAM Action (MEDIUM)

**CDK (`compute.py:97`):** Includes `bedrock:Converse` in Bedrock permissions.

**Terraform (`iam.tf:86-89`):** Only has `bedrock:InvokeModel`, `bedrock:StartAsyncInvoke`, `bedrock:GetAsyncInvoke`, `bedrock:ListAsyncInvokes`. Missing `bedrock:Converse`.

**Impact:** `prompt_analyzer.py:54` calls `bedrock_client.converse()` for LLM-based query weight analysis. `bedrock:Converse` is a separate IAM action from `bedrock:InvokeModel`. If the Terraform deployment was running without this permission, the Converse API call would fail, and the weight analyzer would fall back to default weights (0.4/0.2/0.2/0.2 per `prompt_analyzer.py:99-104`).

This means:
- **Terraform deployment** may have been using **fallback weights** for every search query
- **CDK deployment** correctly uses **LLM-analyzed weights** tailored to each query
- This would cause different search result rankings between the two deployments

**Note:** This is a bug in the *original* Terraform deployment, not the CDK migration. The CDK version correctly adds `bedrock:Converse`. However, if the Terraform deployment was "working" with fallback weights and users expect those results, the CDK deployment's dynamic weights will produce different rankings.

---

### ISSUE 3 — `NOVA_ANALYZER_MODEL_ID` Env Var Difference (LOW)

**Terraform (`lambda.tf:33`):** Sets `NOVA_ANALYZER_MODEL_ID` to a custom model deployment ARN:
```
arn:aws:bedrock:us-east-1:376678947624:custom-model-deployment/4ezx3jono3xp
```

**CDK (`compute.py:40,204`):** Sets `NOVA_ANALYZER_MODEL_ID` from CDK context, defaulting to `anthropic.claude-haiku-4-5-20251001-v1:0` (no `global.` prefix).

**Effective impact: NONE at search time.** Here's why:
1. `search_function.py:26` reads `CLAUDE_MODEL_ID` (not `NOVA_ANALYZER_MODEL_ID`) and passes it as `analyzer_model_id` to `search_with_fusion()`
2. `prompt_analyzer.py:50` uses the passed `analyzer_model_id` parameter, falling back to `DEFAULT_MODEL_ID` only if None
3. Both deployments set `CLAUDE_MODEL_ID` to `global.anthropic.claude-haiku-4-5-20251001-v1:0`
4. So the effective model for weight analysis is `CLAUDE_MODEL_ID` in both cases

The `NOVA_ANALYZER_MODEL_ID` env var on the search Lambda is effectively dead code — it's only used as the `DEFAULT_MODEL_ID` fallback, which is overridden by the explicit `analyzer_model_id` parameter.

---

### ISSUE 4 — CDK Does Not Auto-Create S3 Vectors Bucket (LOW)

**Terraform:** Uses a `null_resource` with a Python script to create the S3 Vectors bucket via boto3.

**CDK:** Only defines the bucket name string. Bucket must be created manually via `s3vectors.create_vector_bucket()`.

**Impact:** Not a search quality issue, but a deployment step that must be done manually with CDK. If the bucket doesn't exist, embedding storage and search will fail entirely (not silently — the pipeline would error out).

---

### ISSUE 5 — CDK Missing `s3vectors:CreateVectorBucket`/`ListVectorBuckets` (LOW)

**Terraform (`iam.tf`):** Grants `s3vectors:*` on all resources.

**CDK (`compute.py:77-84`):** Grants specific actions: `CreateIndex`, `DeleteIndex`, `ListIndexes`, `PutVectors`, `GetVectors`, `DeleteVectors`, `QueryVectors`.

**Impact:** Missing `CreateVectorBucket`, `DeleteVectorBucket`, `ListVectorBuckets`, `GetVectorBucket`. These are only needed for bucket management (not vector CRUD), and the CLAUDE.md notes say bucket creation is manual. Normal ingestion and search operations are unaffected.

---

### ISSUE 6 — OpenSearch Access Policy Scope (LOW)

**Terraform:** Allows `es:*` from any principal in the same AWS account.

**CDK:** Allows `es:ESHttp*` only to the specific Lambda role ARN.

**Impact:** No functional difference for the application. CDK is more secure but will block manual OpenSearch queries from other IAM roles (e.g., debugging from a developer's IAM user).

---

## Infrastructure Settings Comparison (Matching)

| Setting | Terraform | CDK | Match? |
|---------|-----------|-----|--------|
| OpenSearch engine version | 2.19 | 2.19 | Yes |
| OpenSearch instance type | or1.medium.search | or1.medium.search | Yes |
| S3 Vectors engine | Enabled | Enabled (L1 escape hatch) | Yes |
| Lambda timeouts | Per-function | Per-function | Yes (all match) |
| Lambda memory | Per-function | Per-function | Yes (all match) |
| DynamoDB table names | `video-search-v2-*` | `video-search-v2-*` | Yes |
| Step Functions flow | Same 7-step pipeline | Same 7-step pipeline | Yes |
| Step Functions `payload_response_only` | N/A (direct invoke) | `True` (equivalent) | Yes |
| Step Functions retry config | States.ALL, 2 retries | States.ALL, 2 retries | Yes |
| ResultPath/InputPath/OutputPath | All states | All states | Yes (all match) |

---

## Root Cause Analysis: Why Search Results Differ

### Primary cause: ISSUE 0 — Nova Lite model not in CDK Bedrock IAM policy

The caption Lambda uses `amazon.nova-lite-v1:0` for captioning and genre classification, but the CDK Bedrock IAM policy only allows `amazon.nova-2-multimodal-embeddings-v1:0` and `global.anthropic.claude-haiku-4-5-20251001-v1:0`. The Terraform deployment used `Resource: "*"` which allowed all models.

This causes:
- **All captions are empty strings** — the `invoke_model` call fails with AccessDeniedException, caught silently
- **All genres are "Other"** — genre classification also fails silently
- **Search scores are very low** — BM25 text matching has no caption content to match against, so only vector similarity contributes to scores
- **This matches the exact symptoms reported by James Wu**: Score 0.300, genre "Other", missing captions

### Secondary cause: ISSUE 2 — Different weight analysis behavior

The Terraform deployment was missing `bedrock:Converse` (used by the weight analyzer), causing fallback weights (0.4/0.2/0.2/0.2) for every query. CDK correctly grants `bedrock:Converse`, so dynamic per-query weights are now used. This changes result rankings but is actually an improvement.

### Additional impact: ISSUE 1 — Missing Rekognition face permissions

Entity face management operations (`CreateCollection`, `IndexFaces`, `SearchFacesByImage`, etc.) are missing from CDK, breaking the `@entity_name` visual re-ranking feature.

---

---

## CloudWatch Log Evidence (2026-03-12)

Logs were checked across all Lambda functions (both deployment log groups). Here is the full error inventory:

### Caption Lambda — CONFIRMED AccessDeniedException
```
AccessDeniedException: User: ...ComputeLambdaExecRole... is not authorized to perform:
bedrock:InvokeModel on resource: arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0
because no identity-based policy allows the bedrock:InvokeModel action
```
**Every caption and genre classification call fails.** All 20 sampled log entries show this error.

### Merge Lambda — S3 Vectors Indices Not Found
```
NotFoundException: The specified index could not be found
```
Affects all three modalities: `nova-visual-{project_id}`, `nova-audio-{project_id}`, `nova-transcription-{project_id}`. Vectors cannot be retrieved during merge → OpenSearch documents are indexed without vector data.

### Transcription Lambda — TWO errors
1. **AccessDeniedException on `transcribe:StartTranscriptionJob`** — despite CDK granting this action scoped to job name prefixes, the permission is being denied. Needs investigation (possibly ARN format mismatch).
2. **S3 Vectors NotFoundException on `PutVectors`** — transcription embeddings can't be stored because the vector index doesn't exist (downstream of Project function failure).

### Search Lambda — Mixed results across deployments
- **Log Group 1**: Weight analysis works (dynamic weights), but `index_not_found_exception` for `segments-{project_id}`.
- **Log Group 2**: `AccessDeniedException` on Bedrock Converse for Claude Haiku cross-region inference profile. Falls back to default weights (0.4/0.2/0.2/0.2).

### ShotSegmentation Lambda — Docker Entrypoint Misconfigured
```
Runtime.InvalidEntrypoint
```
One log group shows every invocation failing at INIT phase. The Docker Lambda's handler/entrypoint is wrong. This is a CDK deployment configuration issue.

### Project Lambda — Root Cause of Index Failures
Two infrastructure prerequisites are missing:
1. **S3 Vectors bucket not found** — `NotFoundException: The specified vector bucket could not be found`
2. **OpenSearch S3 Vectors engine not enabled** — `RequestError(400): Failed to parse mapping: Please enable s3vector on the domain to use it as a vector engine`

This is the **root cause** of the Merge and Transcription vector failures — the Project function can't create vector indices or OpenSearch indices, so downstream Lambdas have nothing to write to or read from.

### Clean Lambdas (no errors)
- CelebrityDetection
- Orchestrator
- Entity
- Upload
- Video

---

## Failure Cascade Diagram

```
PROJECT CREATION
    │
    ├─ S3 Vectors bucket missing ──────────► Cannot create vector indices
    │                                              │
    │                                    ┌─────────┼─────────┐
    │                                    ▼         ▼         ▼
    │                              Embedding   Transcription  Merge
    │                              PutVectors  PutVectors    GetVectors
    │                              (silent)    (FAILS)       (FAILS)
    │
    └─ OpenSearch S3 Vectors engine ───────► Cannot create segments index
       not enabled                                  │
                                                    ▼
                                              Search returns
                                              index_not_found

INGESTION PIPELINE
    │
    ├─ ShotSegmentation ─── Runtime.InvalidEntrypoint (Docker config)
    │
    ├─ Caption Lambda ───── AccessDeniedException (nova-lite-v1:0 not in IAM)
    │                       → empty captions, genre = "Other"
    │
    ├─ Transcription ────── AccessDeniedException (StartTranscriptionJob denied)
    │                       → no transcription text
    │
    └─ Merge Lambda ─────── NotFoundException (vector indices missing)
                            → documents indexed without vectors

SEARCH
    │
    ├─ Weight analysis ──── AccessDeniedException (Converse API, intermittent)
    │                       → fallback weights some of the time
    │
    └─ OpenSearch query ─── index_not_found_exception
                            → no results or degraded results
```

---

## Revised Issue Inventory (from logs)

| # | Issue | Severity | Lambda | Log Evidence |
|---|-------|----------|--------|-------------|
| 0 | Nova Lite model missing from Bedrock IAM | CRITICAL | Caption | AccessDeniedException on every invocation |
| 1 | S3 Vectors bucket not created | CRITICAL | Project, Merge, Transcription | NotFoundException on vector bucket and indices |
| 2 | OpenSearch S3 Vectors engine not enabled | CRITICAL | Project | RequestError(400) "Please enable s3vector" |
| 3 | ShotSegmentation Docker entrypoint wrong | CRITICAL | ShotSegmentation | Runtime.InvalidEntrypoint on every invocation |
| 4 | Transcribe IAM permission denied | HIGH | Transcription | AccessDeniedException on StartTranscriptionJob |
| 5 | Bedrock Converse ARN pattern incomplete | MEDIUM | Search | AccessDeniedException (intermittent, one log group) |
| 6 | Missing Rekognition face permissions | HIGH | (Entity/Project — not yet triggered) | Not yet in logs (entity face features not tested) |
| 7 | OpenSearch index missing for projects | HIGH | Search | index_not_found_exception |

---

## Recommended Actions (Priority Order)

### Infrastructure prerequisites (must fix first)
1. **Create S3 Vectors bucket** manually via `s3vectors.create_vector_bucket()` — this unblocks Project, Merge, and Transcription vector operations
2. **Enable S3 Vectors engine on OpenSearch domain** — either via L1 escape hatch in CDK or manual `update_domain_config()` via boto3
3. **Fix ShotSegmentation Docker entrypoint** in CDK — check `compute.py` Docker Lambda handler configuration

### IAM permission fixes (CDK code changes)
4. **Add `amazon.nova-lite-v1:0`** to Bedrock IAM policy (`compute.py:101-108`)
5. **Fix Transcribe IAM permissions** — investigate ARN scoping (job name prefix pattern may not match)
6. **Fix Bedrock Converse ARN patterns** — ensure all three ARN patterns for cross-region Claude inference profiles are correct
7. **Add missing Rekognition face permissions** (`compute.py:116-123`)

### After all fixes deployed
8. **Re-ingest all videos** — existing videos have empty captions, missing transcriptions, missing vectors, and incomplete OpenSearch documents
