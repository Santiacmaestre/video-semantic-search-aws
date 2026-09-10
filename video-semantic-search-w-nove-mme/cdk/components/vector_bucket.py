"""S3 Vectors bucket — the vector store behind per-modality kNN search.

Holds the 1024-dimensional Nova MME embeddings in four indexes per project
(visual, audio, transcription, entity), created at runtime by
vector_store.create_project_indices(). It is also what OpenSearch delegates kNN
storage to when a project uses vector_engine='s3_vectors': the index maps its
knn_vector fields with {"engine": "s3vector"}, so the vectors live here instead
of in cluster memory.

The bucket used to be created by hand before deploying. It is a CfnVectorBucket
(L1) because aws-cdk-lib ships no L2 for S3 Vectors yet, which also means there
is no auto_delete_objects equivalent — hence the cleanup custom resource below.
"""
import os

from constructs import Construct
from aws_cdk import (
    CustomResource,
    Duration,
    RemovalPolicy,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3vectors as s3vectors,
    custom_resources as cr,
    BundlingOptions,
)
from config import LAMBDA_RUNTIME

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class VectorBucketConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        project_name: str,
        account_id: str,
    ) -> None:
        super().__init__(scope, id)

        self.bucket_name = f"{project_name}-vectors-{account_id}"

        self.bucket = s3vectors.CfnVectorBucket(
            self, "Resource",
            vector_bucket_name=self.bucket_name,
            # SSE-S3. S3 Vectors always encrypts at rest; stating it keeps the
            # posture explicit in the template rather than relying on the default.
            encryption_configuration=s3vectors.CfnVectorBucket.EncryptionConfigurationProperty(
                sse_type="AES256",
            ),
        )
        # Matches every other data store in this stack. Safe because the vectors
        # are derived data, rebuildable from the videos bucket.
        self.bucket.apply_removal_policy(RemovalPolicy.DESTROY)

        self.bucket_arn = self.bucket.attr_vector_bucket_arn

        # --- Empty the bucket on stack delete ---
        # S3 Vectors rejects DeleteVectorBucket while any index remains, so the
        # DESTROY policy above cannot succeed on its own.
        cleanup_role = iam.Role(
            self, "CleanupRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )
        # S3 Vectors has no resource-level IAM support, so these cannot be
        # scoped to the bucket ARN.
        cleanup_role.add_to_policy(iam.PolicyStatement(
            actions=["s3vectors:ListIndexes", "s3vectors:DeleteIndex"],
            resources=["*"],
        ))

        cleanup_fn = lambda_.Function(
            self, "CleanupFunction",
            runtime=LAMBDA_RUNTIME,
            handler="vector_bucket_function.lambda_handler",
            code=lambda_.Code.from_asset(
                os.path.join(PROJECT_ROOT, "lambda", "functions"),
                bundling=BundlingOptions(
                    image=LAMBDA_RUNTIME.bundling_image,
                    command=[
                        "bash", "-c",
                        "cp /asset-input/vector_bucket_function.py /asset-output/",
                    ],
                ),
            ),
            role=cleanup_role,
            memory_size=512,
            timeout=Duration.minutes(5),
            log_retention=logs.RetentionDays.ONE_WEEK,
        )

        provider = cr.Provider(
            self, "CleanupProvider",
            on_event_handler=cleanup_fn,
        )

        cleanup = CustomResource(
            self, "Cleanup",
            service_token=provider.service_token,
            properties={"VectorBucketName": self.bucket_name},
        )
        # Creation order is bucket then cleanup, so teardown runs in reverse:
        # the cleanup resource empties the bucket before CloudFormation deletes it.
        cleanup.node.add_dependency(self.bucket)
