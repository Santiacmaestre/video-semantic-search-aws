# Fargate Shot Segmentation Design

## Problem

The shot segmentation step in the Step Functions video processing pipeline runs on a Docker-based Lambda with a hard 15-minute timeout. This limits video processing to roughly 30-60 minute files. We need to support videos up to 4-6 hours (conference talks, full movies, long events).

## Decision Summary

- **Full replacement**: All shot segmentation runs on Fargate, regardless of video size. No conditional routing.
- **Step Functions native integration**: Use `EcsRunTask` with `.sync` pattern. Step Functions polls task status automatically.
- **Fixed resource allocation**: Single task definition with 2 vCPU, 8 GB RAM, 30 GB ephemeral storage.
- **Reuse existing Docker image**: Adapt the current `deployment/Dockerfile` with a new base image and entrypoint.

## Architecture

### Updated Pipeline Flow

```
S3 Upload -> Orchestrator -> EcsRunTask (Fargate shot segmentation, .sync)
                                    |
                          ReadSegmentationResult (Lambda, reads S3 output)
                                    |
                             PrepareParallel
                                    |
                    +---------------+---------------+
                    v               v               v
               Nova MME        Transcribe      Rekognition
                    +---------------+---------------+
                                    v
                           Caption + Genre
                                    |
                                    v
                              Merge Lambda
```

### Docker Image Changes

1. **Change base image** from `public.ecr.aws/lambda/python:3.11` to `python:3.13-slim` (matching project's Python 3.13 runtime). The image no longer serves as a Lambda runtime.

2. **Add `entrypoint.py`** in `deployment/`:
   - Reads JSON input from `TASK_INPUT` environment variable (passed via Step Functions container overrides)
   - Calls core segmentation logic extracted from `shot_segmentation_function.py`
   - Writes result JSON to `s3://{videos_bucket}/processing/{video_id}/segmentation_result.json`
   - Exits 0 on success, non-zero on failure

3. **Refactor `shot_segmentation_function.py`**: Extract core segmentation logic (scene detection, segment building, clip extraction) into a standalone callable function separate from `lambda_handler`. The `entrypoint.py` imports and calls this directly.

4. **Move celebrity_detection and caption to zip-based Lambdas**: These functions share the Docker image today but don't need ffmpeg. They move to standard zip-packaged Lambdas using the shared Lambda layer, matching how the other API/pipeline Lambdas are already packaged.

### CDK Infrastructure (new file: `cdk/components/container.py`)

1. **VPC**: Minimal VPC with 2 AZs and public subnets only. No NAT gateway needed.

2. **ECS Cluster**: Fargate-only cluster in the VPC. Logical grouping, no EC2 instances.

3. **Fargate Task Definition**:
   - 2 vCPU, 8 GB memory, 30 GB ephemeral storage
   - Container built from updated `deployment/Dockerfile`
   - Task execution role: ECR pull, CloudWatch logs
   - Task role: S3 read/write on videos bucket
   - `TASK_INPUT` environment variable overridden per-run

4. **`ReadSegmentationResult` Lambda**: Lightweight function (~20 lines) that reads the segmentation result JSON from S3 and returns it as state output. Bridges ECS task output into the Step Functions data flow.

### Step Functions State Machine Changes (`processing.py`)

1. **Replace** `LambdaInvoke` for ShotSegmentation with `EcsRunTask`:
   - Integration pattern: `.sync` (wait for completion)
   - Container overrides pass state input as `TASK_INPUT` env var
   - Subnet: public, `assign_public_ip=True`

2. **Add** `ReadSegmentationResult` state after EcsRunTask:
   - Invokes the bridge Lambda to read result from S3
   - Output feeds into `PrepareParallel` (unchanged)

3. **Timeout**: 3600 seconds (1 hour) on the ECS RunTask state.

4. **Retries**: Same as current — 2 attempts, 10s interval, 2x backoff.

### Pipeline Integration

- **Output bridging**: ECS RunTask `.sync` returns task status, not custom output. The Fargate container writes result JSON to a known S3 key (`processing/{video_id}/segmentation_result.json`). The `ReadSegmentationResult` Lambda reads and returns it.
- **Downstream unchanged**: Everything after `PrepareParallel` remains the same — parallel branches, caption generation, merge.
- **Cleanup**: The S3 result file at `processing/{video_id}/` can be cleaned up by the Merge Lambda at pipeline end.
- **Error handling**: No change. Retry-then-fail behavior is preserved. The pre-existing issue of videos stuck in "processing" on failure is out of scope.

## Files to Create

| File | Purpose |
|------|---------|
| `deployment/entrypoint.py` | Fargate entrypoint — reads input, runs segmentation, writes output to S3 |
| `cdk/components/container.py` | CDK construct for VPC, ECS cluster, task definition |
| `lambda/functions/read_segmentation_result_function.py` | Bridge Lambda to read Fargate output from S3 |

## Files to Modify

| File | Change |
|------|--------|
| `deployment/Dockerfile` | New base image (`python:3.13-slim`), new entrypoint, remove Lambda runtime adapter |
| `lambda/functions/shot_segmentation_function.py` | Extract core logic into importable function |
| `cdk/components/processing.py` | Replace Lambda invoke with EcsRunTask + ReadSegmentationResult states |
| `cdk/components/compute.py` | Remove shot_segmentation Docker Lambda, add celebrity_detection and caption as zip Lambdas |
| `cdk/stacks/video_search_stack.py` | Wire in new ContainerConstruct, pass references between constructs |

## Cost Estimate

- **Fargate**: ~$0.04/vCPU-hour + ~$0.004/GB-hour. A 30-min task with 2 vCPU / 8 GB costs ~$0.06. A 1-hour task costs ~$0.11.
- **No NAT gateway**: Using public subnet with assigned public IP saves ~$32/month.
- **Bridge Lambda**: Negligible cost (runs <1s per invocation).
- **VPC**: No cost for the VPC itself. Only pay for Fargate tasks when running.
