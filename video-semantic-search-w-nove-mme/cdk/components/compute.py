import os
from constructs import Construct
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
from config import LAMBDA_RUNTIME, NOVA_MODEL_ID, NOVA_LITE_MODEL_ID, CLAUDE_MODEL_ID

# Absolute path to project root (parent of cdk/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ComputeConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        project_name: str,
        account_id: str,
        videos_bucket: s3.IBucket,
        static_bucket: s3.IBucket,
        videos_table: dynamodb.ITableV2,
        segments_table: dynamodb.ITableV2,
        entities_table: dynamodb.ITableV2,
        projects_table: dynamodb.ITableV2,
        processing_queue: sqs.IQueue,
        vector_bucket_name: str,
    ) -> None:
        super().__init__(scope, id)

        nova_analyzer_model_id = scope.node.try_get_context("nova_analyzer_model_id") or "anthropic.claude-haiku-4-5-20251001-v1:0"

        # --- Shared IAM Role ---
        self.lambda_role = iam.Role(
            self, "LambdaExecRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )

        # Grant DynamoDB access
        for table in [videos_table, segments_table, entities_table, projects_table]:
            table.grant_read_write_data(self.lambda_role)
        # Grant access to GSI on projects table
        self.lambda_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "dynamodb:Query",
            ],
            resources=[f"{projects_table.table_arn}/index/*"],
        ))

        # Grant S3 access
        videos_bucket.grant_read_write(self.lambda_role)
        self.lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
            resources=[
                f"arn:aws:s3:::{vector_bucket_name}/*",
                f"arn:aws:s3:::{vector_bucket_name}",
            ],
            conditions={
                "StringEquals": {"s3:ResourceAccount": account_id},
            },
        ))

        # S3 Vectors (no resource-level IAM support)
        self.lambda_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "s3vectors:CreateIndex",
                "s3vectors:DeleteIndex",
                "s3vectors:ListIndexes",
                "s3vectors:PutVectors",
                "s3vectors:GetVectors",
                "s3vectors:DeleteVectors",
                "s3vectors:QueryVectors",
            ],
            resources=["*"],
        ))

        # SQS
        processing_queue.grant_send_messages(self.lambda_role)
        processing_queue.grant_consume_messages(self.lambda_role)

        # Bedrock — model invocations scoped to specific models
        self.lambda_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel",
                "bedrock:Converse",
                "bedrock:StartAsyncInvoke",
                "bedrock:GetAsyncInvoke",
            ],
            resources=[
                f"arn:aws:bedrock:us-east-1::foundation-model/{NOVA_MODEL_ID}",
                # Nova Lite (us.* cross-region inference profile for captions/genre)
                f"arn:aws:bedrock:us-east-1::foundation-model/{NOVA_LITE_MODEL_ID}",
                f"arn:aws:bedrock:us-east-1:{account_id}:inference-profile/{NOVA_LITE_MODEL_ID}",
                f"arn:aws:bedrock:*::foundation-model/{NOVA_LITE_MODEL_ID.replace('us.', '')}",
                # Claude (global.* cross-region inference profile for weight analysis)
                f"arn:aws:bedrock:us-east-1::foundation-model/{CLAUDE_MODEL_ID}",
                f"arn:aws:bedrock:us-east-1:{account_id}:inference-profile/{CLAUDE_MODEL_ID}",
                f"arn:aws:bedrock:*::foundation-model/{CLAUDE_MODEL_ID.replace('global.', '')}",
            ],
        ))
        # Bedrock — list operations (no resource-level support)
        self.lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:ListAsyncInvokes"],
            resources=["*"],
        ))

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

        # Transcribe (scoped to job name prefix)
        self.lambda_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "transcribe:StartTranscriptionJob",
                "transcribe:GetTranscriptionJob",
                "transcribe:DeleteTranscriptionJob",
            ],
            resources=[
                f"arn:aws:transcribe:us-east-1:{account_id}:transcription-job/nova-transcribe-*",
                f"arn:aws:transcribe:us-east-1:{account_id}:transcription-job/sfn-transcribe-*",
            ],
        ))

        # Step Functions — permission added later via add_state_machine_permissions()

        # --- Lambda Layer ---
        self.shared_layer = lambda_.LayerVersion(
            self, "SharedLayer",

            compatible_runtimes=[LAMBDA_RUNTIME],
            code=lambda_.Code.from_asset(
                os.path.join(PROJECT_ROOT, "lib"),
                bundling=BundlingOptions(
                    image=LAMBDA_RUNTIME.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install opensearch-py requests-aws4auth -t /asset-output/python/ && "
                        "cp -r /asset-input/*.py /asset-output/python/",
                    ],
                ),
            ),
        )

        # --- Helper to create zip Lambda functions ---
        def _make_zip_lambda(
            func_id: str,
            handler: str,
            memory: int = 512,
            timeout_secs: int = 30,
            log_retention_days: int = 7,
            use_layer: bool = False,
            environment: dict | None = None,
        ) -> lambda_.Function:
            fn = lambda_.Function(
                self, func_id,

                runtime=LAMBDA_RUNTIME,
                handler=handler,
                code=lambda_.Code.from_asset(
                    os.path.join(PROJECT_ROOT, "lambda", "functions"),
                    bundling=BundlingOptions(
                        image=LAMBDA_RUNTIME.bundling_image,
                        command=[
                            "bash", "-c",
                            f"cp /asset-input/{handler.split('.')[0]}.py /asset-output/",
                        ],
                    ),
                ),
                role=self.lambda_role,
                memory_size=memory,
                timeout=Duration.seconds(timeout_secs),
                log_retention=logs.RetentionDays.ONE_WEEK if log_retention_days == 7 else logs.RetentionDays.TWO_WEEKS,
                layers=[self.shared_layer] if use_layer else [],
                environment=environment or {},
            )
            return fn

        # --- API Lambda Functions ---

        self.search_fn = _make_zip_lambda(
            "SearchFunction", "search_function.lambda_handler",
            use_layer=True,
            environment={
                "S3_VECTOR_BUCKET": vector_bucket_name,
                "VIDEOS_TABLE": videos_table.table_name,
                "SEGMENTS_TABLE": segments_table.table_name,
                "ENTITIES_TABLE": entities_table.table_name,
                "PROJECTS_TABLE": projects_table.table_name,
                "CLAUDE_MODEL_ID": CLAUDE_MODEL_ID,
                "NOVA_ANALYZER_MODEL_ID": nova_analyzer_model_id,
            },
        )

        self.upload_fn = _make_zip_lambda(
            "UploadFunction", "upload_function.lambda_handler",
            environment={
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
                "VIDEOS_TABLE": videos_table.table_name,
                "SQS_QUEUE_URL": processing_queue.queue_url,
            },
        )

        self.video_fn = _make_zip_lambda(
            "VideoFunction", "video_function.lambda_handler",
            environment={
                "VIDEOS_TABLE": videos_table.table_name,
            },
        )

        self.entity_fn = _make_zip_lambda(
            "EntityFunction", "entity_function.lambda_handler",
            environment={
                "ENTITIES_TABLE": entities_table.table_name,
                "PROJECTS_TABLE": projects_table.table_name,
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
                "S3_VECTOR_BUCKET": vector_bucket_name,
            },
        )

        self.project_fn = _make_zip_lambda(
            "ProjectFunction", "project_function.lambda_handler",
            use_layer=True,
            environment={
                "PROJECTS_TABLE": projects_table.table_name,
                "VIDEOS_TABLE": videos_table.table_name,
                "SEGMENTS_TABLE": segments_table.table_name,
                "ENTITIES_TABLE": entities_table.table_name,
                "S3_VECTOR_BUCKET": vector_bucket_name,
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
            },
        )

        # --- Pipeline Lambda Functions ---

        self.orchestrator_fn = _make_zip_lambda(
            "OrchestratorFunction", "orchestrator_function.lambda_handler",
            memory=256, timeout_secs=30, log_retention_days=14,
            environment={
                "VIDEOS_TABLE": videos_table.table_name,
                "PROJECTS_TABLE": projects_table.table_name,
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
                "AWS_ACCOUNT_ID": account_id,
            },
        )

        self.embedding_fn = _make_zip_lambda(
            "EmbeddingFunction", "embedding_function.lambda_handler",
            timeout_secs=900, log_retention_days=14, use_layer=True,
            environment={
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
                "S3_VECTOR_BUCKET": vector_bucket_name,
                "NOVA_MODEL_ID": NOVA_MODEL_ID,
                "AWS_ACCOUNT_ID": account_id,
            },
        )

        self.transcription_fn = _make_zip_lambda(
            "TranscriptionFunction", "transcription_function.lambda_handler",
            timeout_secs=900, log_retention_days=14, use_layer=True,
            environment={
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
                "S3_VECTOR_BUCKET": vector_bucket_name,
                "NOVA_MODEL_ID": NOVA_MODEL_ID,
            },
        )

        self.celebrity_detection_fn = _make_zip_lambda(
            "CelebrityDetectionFunction", "celebrity_detection_function.lambda_handler",
            memory=256, timeout_secs=600, log_retention_days=14,
            environment={
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
            },
        )

        self.caption_fn = _make_zip_lambda(
            "CaptionFunction", "caption_function.handler",
            timeout_secs=600, log_retention_days=14,
            environment={
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
            },
        )

        self.merge_fn = _make_zip_lambda(
            "MergeFunction", "merge_function.lambda_handler",
            timeout_secs=120, log_retention_days=14, use_layer=True,
            environment={
                "VIDEOS_TABLE": videos_table.table_name,
                "SEGMENTS_TABLE": segments_table.table_name,
                "PROJECTS_TABLE": projects_table.table_name,
                "S3_VECTOR_BUCKET": vector_bucket_name,
                "S3_VIDEO_BUCKET": videos_bucket.bucket_name,
            },
        )

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

        # --- S3 trigger for orchestrator ---
        videos_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(self.orchestrator_fn),
            s3.NotificationKeyFilter(prefix="uploads/"),
        )

    def add_opensearch_permissions(self, opensearch_domain_arn: str) -> None:
        """Add OpenSearch HTTP permissions to the Lambda role (called after search construct is created)."""
        self.lambda_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "es:ESHttpGet",
                "es:ESHttpPut",
                "es:ESHttpPost",
                "es:ESHttpDelete",
                "es:ESHttpHead",
            ],
            resources=[f"{opensearch_domain_arn}/*"],
        ))

    def set_opensearch_endpoint(self, endpoint: str) -> None:
        """Set OPENSEARCH_ENDPOINT env var on functions that need it."""
        for fn in [self.search_fn, self.project_fn, self.merge_fn]:
            fn.add_environment("OPENSEARCH_ENDPOINT", f"https://{endpoint}")

    def add_state_machine_permissions(self, state_machine_arn: str) -> None:
        """Add Step Functions StartExecution permission scoped to a specific state machine.

        Uses a separate Policy resource to avoid circular dependency between
        the role's default policy, Lambda functions, and the state machine.
        """
        iam.Policy(
            self, "StepFunctionsPolicy",
            roles=[self.lambda_role],
            statements=[
                iam.PolicyStatement(
                    actions=["states:StartExecution"],
                    resources=[state_machine_arn],
                ),
            ],
        )

    def set_state_machine_arn(self, arn: str) -> None:
        """Set STATE_MACHINE_ARN on the orchestrator function."""
        self.orchestrator_fn.add_environment("STATE_MACHINE_ARN", arn)
