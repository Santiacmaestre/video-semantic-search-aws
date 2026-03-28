"""Bootstrap construct — seeds demo user and sample video on first deploy."""
import os
from constructs import Construct
from aws_cdk import (
    CustomResource,
    Duration,
    Size,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_logs as logs,
    aws_cognito as cognito,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    custom_resources as cr,
    BundlingOptions,
)
from config import LAMBDA_RUNTIME

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class BootstrapConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        user_pool: cognito.IUserPool,
        videos_bucket: s3.IBucket,
        videos_table: dynamodb.ITableV2,
        projects_table: dynamodb.ITableV2,
        lambda_role: iam.IRole,
        shared_layer: lambda_.ILayerVersion,
        vector_bucket_name: str,
        opensearch_endpoint: str,
    ) -> None:
        super().__init__(scope, id)

        bootstrap_fn = lambda_.Function(
            self, "BootstrapFunction",
            runtime=LAMBDA_RUNTIME,
            handler="bootstrap_function.lambda_handler",
            code=lambda_.Code.from_asset(
                os.path.join(PROJECT_ROOT, "lambda", "functions"),
                bundling=BundlingOptions(
                    image=LAMBDA_RUNTIME.bundling_image,
                    command=[
                        "bash", "-c",
                        "cp /asset-input/bootstrap_function.py /asset-input/cfnresponse.py /asset-output/",
                    ],
                ),
            ),
            role=lambda_role,
            layers=[shared_layer],
            memory_size=1024,
            timeout=Duration.minutes(5),
            ephemeral_storage_size=Size.mebibytes(1024),
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "OPENSEARCH_ENDPOINT": f"https://{opensearch_endpoint}",
                "S3_VECTOR_BUCKET": vector_bucket_name,
            },
        )

        provider = cr.Provider(
            self, "BootstrapProvider",
            on_event_handler=bootstrap_fn,
        )

        self.resource = CustomResource(
            self, "BootstrapResource",
            service_token=provider.service_token,
            properties={
                "UserPoolId": user_pool.user_pool_id,
                "ProjectsTable": projects_table.table_name,
                "VideosTable": videos_table.table_name,
                "VideoBucket": videos_bucket.bucket_name,
                "VectorBucket": vector_bucket_name,
            },
        )

        self.demo_email = self.resource.get_att_string("DemoEmail")
        self.demo_password = self.resource.get_att_string("DemoPassword")
