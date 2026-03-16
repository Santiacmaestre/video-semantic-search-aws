from constructs import Construct
from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_sqs as sqs,
)


class StorageConstruct(Construct):
    def __init__(self, scope: Construct, id: str, project_name: str, account_id: str) -> None:
        super().__init__(scope, id)

        # --- S3 Buckets ---

        access_logs_bucket = s3.Bucket(
            self, "AccessLogsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(expiration=Duration.days(90)),
            ],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        self.videos_bucket = s3.Bucket(
            self, "VideosBucket",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="cleanup-temp-files",
                    prefix="frames/",
                    expiration=Duration.days(30),
                ),
                s3.LifecycleRule(
                    id="cleanup-face-crops",
                    prefix="faces/",
                    expiration=Duration.days(30),
                ),
            ],
            server_access_logs_bucket=access_logs_bucket,
            server_access_logs_prefix="videos-access/",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        self.static_bucket = s3.Bucket(
            self, "StaticBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            server_access_logs_bucket=access_logs_bucket,
            server_access_logs_prefix="static-access/",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # --- DynamoDB Tables ---

        self.videos_table = dynamodb.TableV2(
            self, "VideosTable",
            table_name=f"{project_name}-videos",
            partition_key=dynamodb.Attribute(name="video_id", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.segments_table = dynamodb.TableV2(
            self, "SegmentsTable",
            table_name=f"{project_name}-segments",
            partition_key=dynamodb.Attribute(name="video_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="segment_id", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.entities_table = dynamodb.TableV2(
            self, "EntitiesTable",
            table_name=f"{project_name}-entities",
            partition_key=dynamodb.Attribute(name="entity_id", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.projects_table = dynamodb.TableV2(
            self, "ProjectsTable",
            table_name=f"{project_name}-projects",
            partition_key=dynamodb.Attribute(name="project_id", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery=True,
            global_secondary_indexes=[
                dynamodb.GlobalSecondaryIndexPropsV2(
                    index_name="user_id-index",
                    partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
                    projection_type=dynamodb.ProjectionType.ALL,
                ),
            ],
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- SQS ---

        self.processing_dlq = sqs.Queue(
            self, "ProcessingDLQ",
            queue_name=f"{project_name}-video-processing-dlq",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )

        self.processing_queue = sqs.Queue(
            self, "ProcessingQueue",
            queue_name=f"{project_name}-video-processing",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            visibility_timeout=Duration.seconds(900),
            retention_period=Duration.days(14),
            receive_message_wait_time=Duration.seconds(20),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.processing_dlq,
            ),
        )

        # --- S3 Vectors bucket name (created via CfnVectorBucket or custom resource) ---
        self.vector_bucket_name = f"{project_name}-vectors-{account_id}"
