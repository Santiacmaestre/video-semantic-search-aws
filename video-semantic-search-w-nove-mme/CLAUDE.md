# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A serverless video semantic search engine on AWS. Users upload videos, which are automatically segmented, embedded, transcribed, captioned, and indexed. Search uses LLM-weighted hybrid fusion of BM25 text matching and multi-modal kNN vector search (visual, audio, transcription).

## Deployment & Infrastructure

Deployment via **CDK** (`cdk/`): Active deployment in **us-east-1**. `cd cdk && source .venv/bin/activate && cdk deploy`

Project name: `video-search-v2`. Lambda runtime: Python 3.13.

**CDK gotchas:**
- Region is hardcoded to `us-east-1` in `cdk/app.py` — override with `env` parameter
- Bedrock `global.*` model IDs (cross-region inference profiles) need THREE ARN patterns in IAM: `foundation-model/global.anthropic...`, `inference-profile/global.anthropic...` (account-scoped), and `foundation-model/anthropic...` (regionless `arn:aws:bedrock:*::`) — the Converse API checks against the regionless base model ARN
- Transcribe job name prefixes differ: `nova-transcribe-*` in `nova_embeddings.py`, `sfn-transcribe-*` in `transcription_function.py` — IAM policy must allow both
- S3 Vectors bucket is NOT created by CDK — must be created manually via `s3vectors.create_vector_bucket()`
- OpenSearch S3 Vectors engine requires L1 escape hatch (see `cdk/components/search.py`) AND may need manual `update_domain_config()` via boto3
- CDK circular dependency between Lambda role, Lambda functions, and Step Functions: use separate `iam.Policy` resource (not `role.add_to_policy()`) for permissions that reference resources depending on the role's Lambda functions — see `compute.py:add_state_machine_permissions()`
- CDK Lambda functions have auto-generated names like `video-search-v2-stack-Compute*`
- Lambda CloudWatch log group names have hash suffixes — discover with `aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/video-search-v2" --region us-east-1`
- `aws s3vectors` CLI subcommand doesn't exist in standard AWS CLI — use boto3 or the Lambda functions to interact with S3 Vectors
- AWS CLI `AWS_DEFAULT_REGION` env var is overridden by profile config — always use `--region us-east-1`

Lambda packaging: API functions are zipped individually, pipeline functions (shot-segmentation, celebrity-detection, caption) run in a Docker container with ffmpeg. Shared library (`lib/*.py`) is packaged as a Lambda layer with `opensearch-py` and `requests-aws4auth`.

There are no tests or linting configured in this repo.

## Architecture

### Video Processing Pipeline (Step Functions)

```
S3 Upload → Orchestrator → Shot Segmentation (ffmpeg scene detection)
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
               Nova MME        Transcribe      Rekognition
            (visual+audio    (speech→text)    (celebrity
             embeddings)                       detection)
                    └───────────────┼───────────────┘
                                    ▼
                           Caption + Genre (Nova Lite)
                                    │
                                    ▼
                              Merge Lambda
                        (OpenSearch + S3 Vectors)
```

The parallel branches converge at `PrepareCaptionInput` (captions need transcription text), then `Merge` assembles OpenSearch documents with all metadata + vectors.

### Hybrid Search Pipeline (search_engine.py)

1. **Parallel preprocessing**: LLM weight analysis (`prompt_analyzer.py`) + 3 text embeddings (visual/audio/transcription purpose) + optional entity embedding lookup — all concurrent via ThreadPoolExecutor.
2. **Query construction**: Modalities with <5% weight are dropped. Builds OpenSearch hybrid query with BM25 `multi_match` (people^3, caption^2, title) + per-modality kNN sub-queries.
3. **Score fusion**: OpenSearch inline pipeline does min-max normalization then weighted arithmetic mean using LLM-assigned weights.
4. **Entity re-rank** (optional): If `@entity_name` syntax is used with additional text, results are post-sorted by blending search score with cosine similarity against entity's image embedding.

### Two Vector Engines

Set per-project at creation time (immutable):
- **S3 Vectors** (default): `{"engine": "s3vector"}` — vectors stored externally
- **OpenSearch nmslib**: `{"engine": "nmslib", "name": "hnsw"}` — vectors in-memory on cluster

Entity embeddings always use S3 Vectors regardless of project setting.

## Navigation Tips

- `cdk/cdk.out/` contains CDK build artifacts (duplicated source copies, zips) — exclude from searches.

## Code Organization

**`lambda/functions/`** — Lambda handlers. Each is self-contained with a `lambda_handler(event, context)`. API Lambda functions (search, entity, project, video, upload) import `lib/` modules directly (bundled in zip). Pipeline functions (merge, caption, celebrity_detection, shot_segmentation, embedding) use `sys.path.insert(0, '/opt/python')` to access the shared Lambda layer.

**`lib/`** — Shared library (deployed as Lambda layer). Key modules:
- `search_engine.py` — top-level `search_with_fusion()` orchestrating the entire search
- `opensearch_client.py` — index CRUD + `hybrid_search()` query builder
- `nova_embeddings.py` — Bedrock API calls for video/text/image embeddings (model: `amazon.nova-2-multimodal-embeddings-v1:0`, 1024 dimensions)
- `prompt_analyzer.py` — LLM query weight analysis via Bedrock Converse API with JSON output parsing and retry
- `vector_store.py` — S3 Vectors CRUD, per-project indices named `nova-{modality}-{project_id}`
- `dynamodb_store.py` — DynamoDB CRUD for videos/segments/entities

**`frontend-static/`** — Vanilla JS SPA (no build step). `js/config.js` is generated by deploy script with API endpoint, Cognito, and CDN URLs.

**`deployment/`** — `Dockerfile` for the Docker-based Lambda (shot segmentation with ffmpeg). Referenced by `compute.py`.

**`cdk/`** — CDK (Python) infrastructure. Stack in `stacks/video_search_stack.py`, components split by concern:
- `components/storage.py` — S3 buckets, DynamoDB tables, SQS queues
- `components/compute.py` — Lambda functions, IAM role, shared layer
- `components/search.py` — OpenSearch domain with S3 Vectors engine (L1 escape hatch)
- `components/processing.py` — Step Functions state machine definition
- `components/api.py`, `auth.py`, `cdn.py`, `monitoring.py`, `frontend_deployment.py`

## Key Patterns

- All vectors are 1024-dimensional float32, cosine similarity.
- S3 Vectors keys follow the pattern: `{video_id}_seg{index:04d}_{modality}` (e.g., `abc123_seg0003_visual`).
- OpenSearch indices are named `segments-{project_id}`.
- Step Functions pass data between states via `ResultPath`; captions are stored in S3 to avoid payload size limits.
- The weight analysis LLM (default: Haiku) returns JSON with weights summing to 1.0 for visual/audio/transcription/metadata. Fallback weights are 0.4/0.2/0.2/0.2 on failure.
- DynamoDB tables: `video-search-v2-videos`, `video-search-v2-segments`, `video-search-v2-entities`, `video-search-v2-projects`.
- `search_function.py` reads `project_id` from `queryStringParameters`, not the POST body.
- ShotSegmentation Lambda uses Docker — must specify `platform=ecr_assets.Platform.LINUX_AMD64` for Apple Silicon builds.
- Step Functions ShotSegmentation has retry but NO catch handler — failures leave videos stuck in "processing" status permanently.

## Security

- Frontend uses `escapeHtml()` on ALL user-controlled data in innerHTML contexts — always use it for new dynamic content
- CSP meta tag includes `'unsafe-inline'` for `script-src` because the app uses ~20+ inline `onclick` handlers — removing it requires full refactor to event delegation
- Clickjacking protection is via CloudFront `ResponseHeadersPolicy` (`X-Frame-Options: DENY`), NOT via CSP `frame-ancestors` (which doesn't work in meta tags)
- Security headers (HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy) are served by CloudFront ResponseHeadersPolicy in `cdn.py`
- Auth tokens stored in localStorage; logout calls Cognito `GlobalSignOut` to revoke server-side
- API Gateway CORS restricted to CloudFront domain (not wildcard); per-method throttling on search (10/s) and upload (5/s)
