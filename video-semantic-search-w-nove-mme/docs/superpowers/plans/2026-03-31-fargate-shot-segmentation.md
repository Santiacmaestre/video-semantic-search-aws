# Fargate Shot Segmentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Lambda-based shot segmentation with a Fargate task to support videos up to 4-6 hours, beyond Lambda's 15-minute timeout.

**Architecture:** Step Functions invokes a Fargate task (EcsRunTask .sync) that runs the same ffmpeg scene detection and clip extraction logic. The Fargate container writes its result JSON to S3. A lightweight bridge Lambda reads the result and feeds it into the existing pipeline. VPC uses public subnets with assigned public IP (no NAT gateway).

**Tech Stack:** AWS CDK (Python), ECS Fargate, Step Functions EcsRunTask integration, Docker, ffmpeg, boto3

**Spec:** `docs/superpowers/specs/2026-03-31-fargate-shot-segmentation-design.md`

**Important context:** There are no tests or linting configured in this repo. Verification is via `cdk synth` and manual testing. Celebrity detection and caption functions are already zip-based Lambdas — only the shot segmentation Lambda uses the Docker image.

---

### Task 1: Refactor shot_segmentation_function.py — extract core logic

**Files:**
- Modify: `lambda/functions/shot_segmentation_function.py`

The core segmentation logic needs to be callable from both the existing `lambda_handler` (kept temporarily for rollback safety) and the new Fargate entrypoint.

- [ ] **Step 1: Extract core function from lambda_handler**

Move the processing logic into a `run_segmentation()` function that takes explicit parameters and returns the result dict. The `lambda_handler` becomes a thin wrapper.

```python
"""Shot segmentation — ffmpeg scene detection with smart segment boundaries."""
import os
import subprocess
import tempfile
import boto3

s3_client = boto3.client('s3')
S3_VIDEO_BUCKET = os.environ.get('S3_VIDEO_BUCKET', '')

SCENE_THRESHOLD = 0.3
MIN_SEGMENT_SEC = 4


def run_segmentation(video_id, s3_uri, target_duration=10, pre_segments=None):
    """Core segmentation logic. Downloads video, detects scenes, extracts clips, uploads to S3.

    Returns dict with 'segments' list and 'video_duration' float.
    """
    max_duration = int(target_duration * 1.5)
    bucket, key = s3_uri.replace('s3://', '').split('/', 1)

    # Download video
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        s3_client.download_fileobj(bucket, key, tmp)
        video_path = tmp.name

    try:
        duration = _get_duration(video_path)
        print(f"Video duration: {duration}s")

        if pre_segments:
            segments = pre_segments
            print(f"Extract-only: {len(segments)} pre-defined segments")
        else:
            scene_changes = _detect_scenes(video_path)
            print(f"Detected {len(scene_changes)} scene changes")
            segments = _build_smart_segments(scene_changes, duration, target_duration, MIN_SEGMENT_SEC, max_duration)
            print(f"Built {len(segments)} smart segments")

        for seg in segments:
            clip_key = f"clips/{video_id}/seg_{seg['segment_index']:04d}.mp4"
            clip_path = f"/tmp/seg_{seg['segment_index']}.mp4"

            subprocess.run([
                'ffmpeg', '-y', '-ss', str(seg['start_sec']), '-to', str(seg['end_sec']),
                '-i', video_path, '-c', 'copy', '-avoid_negative_ts', '1', clip_path
            ], capture_output=True)

            s3_client.upload_file(clip_path, S3_VIDEO_BUCKET, clip_key)
            seg['clip_s3_uri'] = f"s3://{S3_VIDEO_BUCKET}/{clip_key}"
            os.unlink(clip_path)

        return {'segments': segments, 'video_duration': duration}

    finally:
        os.unlink(video_path)


def lambda_handler(event, context):
    return run_segmentation(
        video_id=event['video_id'],
        s3_uri=event['s3_uri'],
        target_duration=event.get('segment_duration', 10),
        pre_segments=event.get('segments'),
    )


def _get_duration(video_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', video_path],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def _detect_scenes(video_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'frame=pts_time', '-of', 'csv=p=0',
         '-f', 'lavfi', f"movie={video_path},select='gt(scene\\,{SCENE_THRESHOLD})'"],
        capture_output=True, text=True
    )
    timestamps = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        try:
            timestamps.append(float(line))
        except ValueError:
            continue
    return sorted(timestamps)


def _build_smart_segments(scene_changes, video_duration, target_duration=10, min_dur=4, max_dur=15):
    segments = []
    current_start = 0.0

    while current_start < video_duration - 1.0:
        ideal_end = current_start + target_duration

        candidates = [t for t in scene_changes
                      if current_start + min_dur <= t <= current_start + max_dur]

        if candidates:
            seg_end = min(candidates, key=lambda t: abs(t - ideal_end))
        else:
            seg_end = current_start + target_duration

        seg_end = min(seg_end, video_duration)

        segments.append({
            'segment_index': len(segments),
            'start_sec': round(current_start, 2),
            'end_sec': round(seg_end, 2)
        })
        current_start = seg_end

    return segments
```

- [ ] **Step 2: Verify the refactored file is syntactically valid**

Run: `cd /Users/kwt/Documents/q-cli-projects/2026/power-video-semantic-search-with-mm-embedding/video-semantic-search-w-nove-mme && python3 -c "import ast; ast.parse(open('lambda/functions/shot_segmentation_function.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add lambda/functions/shot_segmentation_function.py
git commit -m "refactor: extract run_segmentation() from lambda_handler for Fargate reuse"
```

---

### Task 2: Create Fargate entrypoint

**Files:**
- Create: `deployment/entrypoint.py`

This is the standalone script that runs inside the Fargate container. It reads input from the `TASK_INPUT` environment variable, calls `run_segmentation()`, and writes the result to S3.

- [ ] **Step 1: Create entrypoint.py**

```python
"""Fargate entrypoint for shot segmentation.

Reads task input from TASK_INPUT env var (JSON), runs segmentation,
writes result JSON to S3 at processing/{video_id}/segmentation_result.json.
"""
import json
import os
import sys
import boto3

# shot_segmentation_function.py is in the same directory in the Docker image
from shot_segmentation_function import run_segmentation

s3_client = boto3.client('s3')


def main():
    task_input_raw = os.environ.get('TASK_INPUT')
    if not task_input_raw:
        print("ERROR: TASK_INPUT environment variable not set")
        sys.exit(1)

    try:
        task_input = json.loads(task_input_raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse TASK_INPUT: {e}")
        sys.exit(1)

    video_id = task_input['video_id']
    s3_uri = task_input['s3_uri']
    target_duration = task_input.get('segment_duration', 10)
    pre_segments = task_input.get('segments')
    bucket = os.environ.get('S3_VIDEO_BUCKET', '')

    print(f"Starting segmentation: video_id={video_id}, s3_uri={s3_uri}")

    result = run_segmentation(
        video_id=video_id,
        s3_uri=s3_uri,
        target_duration=target_duration,
        pre_segments=pre_segments,
    )

    # Write result to S3 for the bridge Lambda to read
    result_key = f"processing/{video_id}/segmentation_result.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=result_key,
        Body=json.dumps(result),
        ContentType='application/json',
    )
    print(f"Result written to s3://{bucket}/{result_key}")
    print(f"Segments: {len(result['segments'])}, Duration: {result['video_duration']}s")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Verify syntax**

Run: `python3 -c "import ast; ast.parse(open('deployment/entrypoint.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add deployment/entrypoint.py
git commit -m "feat: add Fargate entrypoint for shot segmentation"
```

---

### Task 3: Update Dockerfile for Fargate

**Files:**
- Modify: `deployment/Dockerfile`

Change from Lambda base image to standard Python. Remove celebrity_detection and caption files (they're already zip Lambdas). Set entrypoint to the new script.

- [ ] **Step 1: Rewrite Dockerfile**

Replace the entire contents of `deployment/Dockerfile` with:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

# Install ffmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl xz-utils && \
    curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz && \
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp && \
    mv /tmp/ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ && \
    mv /tmp/ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ && \
    rm -rf /tmp/ffmpeg* && \
    apt-get purge -y curl xz-utils && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir boto3

# Copy application code
COPY lambda/functions/shot_segmentation_function.py /app/
COPY deployment/entrypoint.py /app/

ENTRYPOINT ["python", "/app/entrypoint.py"]
```

- [ ] **Step 2: Commit**

```bash
git add deployment/Dockerfile
git commit -m "feat: convert Dockerfile from Lambda base to Fargate with python:3.13-slim"
```

---

### Task 4: Create ReadSegmentationResult bridge Lambda

**Files:**
- Create: `lambda/functions/read_segmentation_result_function.py`

This lightweight Lambda reads the segmentation result JSON from S3 and returns it, bridging the Fargate task output into the Step Functions data flow.

- [ ] **Step 1: Create the bridge Lambda**

```python
"""Bridge Lambda — reads Fargate segmentation result from S3 and returns it."""
import json
import os
import boto3

s3_client = boto3.client('s3')
S3_VIDEO_BUCKET = os.environ.get('S3_VIDEO_BUCKET', '')


def lambda_handler(event, context):
    video_id = event['video_id']
    result_key = f"processing/{video_id}/segmentation_result.json"

    resp = s3_client.get_object(Bucket=S3_VIDEO_BUCKET, Key=result_key)
    result = json.loads(resp['Body'].read())

    print(f"Read segmentation result: {len(result['segments'])} segments, {result['video_duration']}s")
    return result
```

- [ ] **Step 2: Verify syntax**

Run: `python3 -c "import ast; ast.parse(open('lambda/functions/read_segmentation_result_function.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add lambda/functions/read_segmentation_result_function.py
git commit -m "feat: add bridge Lambda to read Fargate segmentation result from S3"
```

---

### Task 5: Create CDK container construct

**Files:**
- Create: `cdk/components/container.py`

New CDK construct that defines the VPC, ECS cluster, Fargate task definition, and the bridge Lambda.

- [ ] **Step 1: Create container.py**

```python
import os
from constructs import Construct
from aws_cdk import (
    Duration,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_ecr_assets as ecr_assets,
    BundlingOptions,
)
from config import LAMBDA_RUNTIME

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ContainerConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        project_name: str,
        videos_bucket: s3.IBucket,
        lambda_role: iam.IRole,
    ) -> None:
        super().__init__(scope, id)

        # --- VPC (public subnets only, no NAT gateway) ---
        self.vpc = ec2.Vpc(
            self, "ProcessingVpc",
            vpc_name=f"{project_name}-processing",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
            ],
        )

        # --- ECS Cluster ---
        self.cluster = ecs.Cluster(
            self, "ProcessingCluster",
            cluster_name=f"{project_name}-processing",
            vpc=self.vpc,
        )

        # --- Task Execution Role (ECR pull + CloudWatch logs) ---
        self.task_execution_role = iam.Role(
            self, "TaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
            ],
        )

        # --- Task Role (S3 access for the running container) ---
        self.task_role = iam.Role(
            self, "TaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        videos_bucket.grant_read_write(self.task_role)

        # --- Task Definition ---
        self.task_definition = ecs.FargateTaskDefinition(
            self, "SegmentationTask",
            cpu=2048,       # 2 vCPU
            memory_limit_mib=8192,  # 8 GB
            ephemeral_storage_gib=30,
            execution_role=self.task_execution_role,
            task_role=self.task_role,
        )

        # Docker image from deployment/Dockerfile
        image = ecs.ContainerImage.from_asset(
            directory=PROJECT_ROOT,
            file="deployment/Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
            exclude=["cdk", "cdk.out", ".venv", "terraform", ".terraform", "node_modules", ".git"],
        )

        self.container = self.task_definition.add_container(
            "segmentation",
            image=image,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="segmentation",
                log_retention=logs.RetentionDays.TWO_WEEKS,
            ),
            environment={
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
            },
        )

        # --- Security Group (outbound only, no inbound) ---
        self.security_group = ec2.SecurityGroup(
            self, "SegmentationSg",
            vpc=self.vpc,
            description="Fargate segmentation task - outbound only",
            allow_all_outbound=True,
        )

        # --- Bridge Lambda (ReadSegmentationResult) ---
        self.read_result_fn = lambda_.Function(
            self, "ReadSegmentationResultFunction",
            runtime=LAMBDA_RUNTIME,
            handler="read_segmentation_result_function.lambda_handler",
            code=lambda_.Code.from_asset(
                os.path.join(PROJECT_ROOT, "lambda", "functions"),
                bundling=BundlingOptions(
                    image=LAMBDA_RUNTIME.bundling_image,
                    command=[
                        "bash", "-c",
                        "cp /asset-input/read_segmentation_result_function.py /asset-output/",
                    ],
                ),
            ),
            role=lambda_role,
            timeout=Duration.seconds(30),
            log_retention=logs.RetentionDays.TWO_WEEKS,
            environment={
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
            },
        )
```

- [ ] **Step 2: Verify syntax**

Run: `cd cdk && python3 -c "import ast; ast.parse(open('components/container.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add cdk/components/container.py
git commit -m "feat: add CDK container construct with VPC, ECS cluster, Fargate task definition"
```

---

### Task 6: Update processing.py — replace Lambda invoke with EcsRunTask

**Files:**
- Modify: `cdk/components/processing.py`

Replace the `LambdaInvoke` for ShotSegmentation with `EcsRunTask` (.sync) + `LambdaInvoke` for ReadSegmentationResult.

- [ ] **Step 1: Update constructor signature**

In `processing.py`, change the constructor to accept ECS resources instead of `shot_segmentation_fn`:

Replace the constructor parameters:

```python
class ProcessingConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        project_name: str,
        shot_segmentation_fn: lambda_.IFunction,
        embedding_fn: lambda_.IFunction,
```

With:

```python
class ProcessingConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        project_name: str,
        cluster: ecs.ICluster,
        task_definition: ecs.FargateTaskDefinition,
        container: ecs.ContainerDefinition,
        subnets: ec2.SubnetSelection,
        security_group: ec2.ISecurityGroup,
        read_result_fn: lambda_.IFunction,
        embedding_fn: lambda_.IFunction,
```

- [ ] **Step 2: Update imports**

Replace the imports at the top of the file:

```python
from constructs import Construct
from aws_cdk import (
    Duration,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_lambda as lambda_,
    aws_logs as logs,
)
```

With:

```python
from constructs import Construct
from aws_cdk import (
    Duration,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_lambda as lambda_,
    aws_logs as logs,
)
```

- [ ] **Step 3: Replace ShotSegmentation state with EcsRunTask + ReadResult**

Replace the shot_segmentation LambdaInvoke block (lines 28-40):

```python
        shot_segmentation = tasks.LambdaInvoke(
            self, "ShotSegmentation",
            lambda_function=shot_segmentation_fn,
            result_path="$.shot_result",
            payload_response_only=True,
            retry_on_service_exceptions=False,
        )
        shot_segmentation.add_retry(
            errors=["States.ALL"],
            interval=Duration.seconds(10),
            max_attempts=2,
            backoff_rate=2,
        )
```

With:

```python
        # Fargate shot segmentation (replaces Lambda)
        shot_segmentation = tasks.EcsRunTask(
            self, "ShotSegmentation",
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            cluster=cluster,
            task_definition=task_definition,
            launch_target=tasks.EcsFargateLaunchTarget(
                platform_version=ecs.FargatePlatformVersion.LATEST,
            ),
            container_overrides=[
                tasks.ContainerOverride(
                    container_definition=container,
                    environment=[
                        tasks.TaskEnvironmentVariable(
                            name="TASK_INPUT",
                            value=sfn.JsonPath.json_to_string(
                                sfn.JsonPath.object_at("$")
                            ),
                        ),
                    ],
                ),
            ],
            subnets=subnets,
            security_groups=[security_group],
            assign_public_ip=True,
            result_path=sfn.JsonPath.DISCARD,
            timeout=Duration.seconds(3600),
        )
        shot_segmentation.add_retry(
            errors=["States.ALL"],
            interval=Duration.seconds(10),
            max_attempts=2,
            backoff_rate=2,
        )

        # Bridge: read Fargate result from S3
        read_segmentation_result = tasks.LambdaInvoke(
            self, "ReadSegmentationResult",
            lambda_function=read_result_fn,
            result_path="$.shot_result",
            payload_response_only=True,
        )
```

- [ ] **Step 4: Update the chain to include ReadSegmentationResult**

Replace the chain definition (line ~198):

```python
        definition = (
            shot_segmentation
            .next(prepare_parallel)
```

With:

```python
        definition = (
            shot_segmentation
            .next(read_segmentation_result)
            .next(prepare_parallel)
```

- [ ] **Step 5: Verify syntax**

Run: `cd cdk && python3 -c "import ast; ast.parse(open('components/processing.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add cdk/components/processing.py
git commit -m "feat: replace Lambda invoke with EcsRunTask for shot segmentation"
```

---

### Task 7: Update compute.py — remove Docker Lambda

**Files:**
- Modify: `cdk/components/compute.py`

Remove the `DockerImageFunction` for shot segmentation. The `celebrity_detection_fn` and `caption_fn` are already zip-based and remain unchanged.

- [ ] **Step 1: Remove the Docker Lambda block**

Delete the entire `# --- Docker-based Lambda (shot segmentation) ---` block (lines 333-352):

```python
        # --- Docker-based Lambda (shot segmentation) ---
        self.shot_segmentation_fn = lambda_.DockerImageFunction(
            self, "ShotSegmentationFunction",

            code=lambda_.DockerImageCode.from_image_asset(
                directory=PROJECT_ROOT,
                file="deployment/Dockerfile",
                cmd=["shot_segmentation_function.lambda_handler"],
                platform=ecr_assets.Platform.LINUX_AMD64,
                exclude=["cdk", "cdk.out", ".venv", "terraform", ".terraform", "node_modules", ".git"],
            ),
            role=self.lambda_role,
            memory_size=1024,
            timeout=Duration.seconds(900),
            log_retention=logs.RetentionDays.TWO_WEEKS,
            ephemeral_storage_size=Size.gibibytes(10),
            environment={
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
            },
        )
```

- [ ] **Step 2: Remove unused imports**

Remove `Size` and `aws_ecr_assets` from the imports since they're no longer used:

Replace:
```python
from aws_cdk import (
    Duration,
    Size,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_sqs as sqs,
    aws_s3_notifications as s3n,
    aws_ecr_assets as ecr_assets,
    BundlingOptions,
)
```

With:
```python
from aws_cdk import (
    Duration,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_sqs as sqs,
    aws_s3_notifications as s3n,
    BundlingOptions,
)
```

- [ ] **Step 3: Verify syntax**

Run: `cd cdk && python3 -c "import ast; ast.parse(open('components/compute.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add cdk/components/compute.py
git commit -m "refactor: remove Docker-based shot segmentation Lambda from compute construct"
```

---

### Task 8: Update video_search_stack.py — wire in container construct

**Files:**
- Modify: `cdk/stacks/video_search_stack.py`

Add the `ContainerConstruct` and update `ProcessingConstruct` to receive ECS resources instead of `shot_segmentation_fn`.

- [ ] **Step 1: Add ContainerConstruct import**

Add to the imports at the top of the file:

```python
from components.container import ContainerConstruct
```

After the line:
```python
from components.compute import ComputeConstruct
```

- [ ] **Step 2: Add container construct instantiation**

Add after the compute construct (after line `# 7. Compute ...` block, around line 120), before the search construct:

```python
        # 7b. Container (Fargate for shot segmentation)
        container = ContainerConstruct(
            self, "Container",
            project_name=project_name,
            videos_bucket=storage.videos_bucket,
            lambda_role=compute.lambda_role,
        )
```

- [ ] **Step 3: Update ProcessingConstruct call**

Replace the ProcessingConstruct instantiation (lines 134-143):

```python
        processing = ProcessingConstruct(
            self, "Processing",
            project_name=project_name,
            shot_segmentation_fn=compute.shot_segmentation_fn,
            embedding_fn=compute.embedding_fn,
            transcription_fn=compute.transcription_fn,
            celebrity_detection_fn=compute.celebrity_detection_fn,
            caption_fn=compute.caption_fn,
            merge_fn=compute.merge_fn,
        )
```

With:

```python
        processing = ProcessingConstruct(
            self, "Processing",
            project_name=project_name,
            cluster=container.cluster,
            task_definition=container.task_definition,
            container=container.container,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=container.security_group,
            read_result_fn=container.read_result_fn,
            embedding_fn=compute.embedding_fn,
            transcription_fn=compute.transcription_fn,
            celebrity_detection_fn=compute.celebrity_detection_fn,
            caption_fn=compute.caption_fn,
            merge_fn=compute.merge_fn,
        )
```

- [ ] **Step 4: Add ec2 import**

Add `aws_ec2 as ec2` to the imports. Replace:

```python
from aws_cdk import (
    Duration,
    Stack,
    Aws,
    CfnOutput,
    aws_cognito as cognito,
    aws_iam as iam,
    aws_s3 as s3,
)
```

With:

```python
from aws_cdk import (
    Duration,
    Stack,
    Aws,
    CfnOutput,
    aws_cognito as cognito,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3 as s3,
)
```

- [ ] **Step 5: Verify syntax**

Run: `cd cdk && python3 -c "import ast; ast.parse(open('stacks/video_search_stack.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add cdk/stacks/video_search_stack.py
git commit -m "feat: wire ContainerConstruct into stack, pass ECS resources to ProcessingConstruct"
```

---

### Task 9: Add ECS RunTask IAM permissions to Step Functions role

**Files:**
- Modify: `cdk/components/container.py`

Step Functions needs `ecs:RunTask`, `ecs:StopTask`, `ecs:DescribeTasks`, and `iam:PassRole` permissions to manage Fargate tasks. CDK's `EcsRunTask` construct grants most of these automatically via the state machine's role, but we need to ensure the task definition's execution role and task role can be passed.

- [ ] **Step 1: Add a method to grant Step Functions permissions**

Add this method to `ContainerConstruct`:

```python
    def grant_state_machine(self, state_machine_role: iam.IRole) -> None:
        """Grant Step Functions role permission to run and monitor ECS tasks."""
        self.task_definition.grant_run(state_machine_role)
```

- [ ] **Step 2: Call grant in video_search_stack.py**

In `video_search_stack.py`, add after the `processing` construct is created (after `compute.set_state_machine_arn(...)`, around line 147):

```python
        # 11b. Grant Step Functions permission to run Fargate tasks
        container.grant_state_machine(processing.state_machine.role)
```

- [ ] **Step 3: Verify syntax of both files**

Run: `cd cdk && python3 -c "import ast; ast.parse(open('components/container.py').read()); print('OK')" && python3 -c "import ast; ast.parse(open('stacks/video_search_stack.py').read()); print('OK')"`
Expected: `OK` twice

- [ ] **Step 4: Commit**

```bash
git add cdk/components/container.py cdk/stacks/video_search_stack.py
git commit -m "feat: grant Step Functions role permission to run Fargate segmentation tasks"
```

---

### Task 10: CDK synth verification

**Files:** None (verification only)

- [ ] **Step 1: Run CDK synth**

Run: `cd cdk && source .venv/bin/activate && cdk synth --quiet 2>&1`
Expected: No errors. The template is synthesized successfully.

- [ ] **Step 2: Inspect the CloudFormation diff**

Run: `cd cdk && cdk diff 2>&1`
Expected: Shows:
- New VPC, subnets, ECS cluster, task definition, security group
- New bridge Lambda (ReadSegmentationResult)
- Removed DockerImageFunction (ShotSegmentation)
- Modified state machine definition (EcsRunTask replaces LambdaInvoke)
- New IAM roles (task execution role, task role)

- [ ] **Step 3: Review the diff for correctness**

Check that:
- The EcsRunTask state has `"Type": "Task"` with `"Resource": "arn:aws:states:::ecs:runTask.sync"`
- Container overrides include `TASK_INPUT` environment variable
- The VPC has no NAT gateway
- The task definition has 2048 CPU, 8192 memory, 30 GB ephemeral
- The bridge Lambda exists with `S3_VIDEO_BUCKET` env var

- [ ] **Step 4: Commit any fixes if needed**

If `cdk synth` revealed issues, fix them and commit.

---

### Task 11: Deploy and test

**Files:** None (deployment and manual testing)

- [ ] **Step 1: Deploy**

Run: `cd cdk && cdk deploy --require-approval broadening 2>&1`

This will take several minutes as it creates the VPC, ECS cluster, and updates the state machine.

- [ ] **Step 2: Test with a short video**

Upload a short test video through the app UI and verify the Step Functions execution completes successfully. Check:
- The ECS task starts and completes in the Fargate cluster
- The segmentation result JSON appears at `processing/{video_id}/segmentation_result.json` in S3
- The bridge Lambda reads the result successfully
- The rest of the pipeline (embeddings, transcription, captions, merge) completes normally
- The video appears as searchable in the app

- [ ] **Step 3: Test with a longer video (if available)**

If you have a video >30 minutes, upload it to verify the Fargate task handles it within the 1-hour timeout.

- [ ] **Step 4: Clean up temporary S3 result file**

Verify the `processing/{video_id}/segmentation_result.json` file exists after pipeline completion. (Cleanup by Merge Lambda is a future improvement, not required for this task.)
