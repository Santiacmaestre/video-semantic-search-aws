"""cdk-nag suppressions for video-search-v2-stack.

All suppressions are centralized here for auditability. Each suppression
includes a business justification explaining why the finding is acceptable.
"""

from aws_cdk import Stack
from cdk_nag import NagSuppressions, NagPackSuppression


def apply_nag_suppressions(stack: Stack) -> None:
    """Apply all cdk-nag suppressions to the stack."""
    _suppress_cdk_internal(stack)
    _suppress_s3(stack)
    _suppress_sqs(stack)
    _suppress_lambda_iam(stack)
    _suppress_opensearch(stack)
    _suppress_api_gateway(stack)
    _suppress_cloudfront(stack)
    _suppress_cognito(stack)
    _suppress_step_functions(stack)
    _suppress_container(stack)


def _suppress_cdk_internal(stack: Stack) -> None:
    """Suppress findings on CDK-generated custom resource Lambdas.

    BucketDeployment, auto_delete_objects, and LogRetention all create
    internal Lambda functions with CDK-managed IAM policies.
    """
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason="CDK-internal custom resource Lambdas use AWS managed policies by design",
                applies_to=[
                    "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                ],
            ),
            NagPackSuppression(
                id="AwsSolutions-IAM5",
                reason="CDK-internal custom resource Lambdas require wildcard permissions for S3 object operations and log group management",
            ),
            NagPackSuppression(
                id="AwsSolutions-L1",
                reason="CDK-internal custom resource Lambda runtimes are managed by the CDK framework",
            ),
        ],
    )


def _suppress_s3(stack: Stack) -> None:
    """Suppress S3 bucket findings."""
    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Storage/AccessLogsBucket/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-S1",
                reason="Access log bucket cannot log to itself — would cause infinite loop",
            ),
        ],
    )

    s10_suppression = [
        NagPackSuppression(
            id="AwsSolutions-S10",
            reason="Bucket access is internal only (Lambda, CloudFront OAC) — all callers use HTTPS by default via AWS SDKs",
        ),
    ]
    for bucket_name in ["AccessLogsBucket", "VideosBucket", "StaticBucket"]:
        for suffix in ["Resource", "Policy/Resource"]:
            NagSuppressions.add_resource_suppressions_by_path(
                stack,
                f"/video-search-v2-stack/Storage/{bucket_name}/{suffix}",
                s10_suppression,
            )


def _suppress_sqs(stack: Stack) -> None:
    """Suppress SQS findings."""
    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Storage/ProcessingDLQ/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-SQS3",
                reason="This IS the dead-letter queue — a DLQ does not need its own DLQ",
            ),
        ],
    )

    for queue_path in [
        "/video-search-v2-stack/Storage/ProcessingDLQ/Resource",
        "/video-search-v2-stack/Storage/ProcessingQueue/Resource",
    ]:
        NagSuppressions.add_resource_suppressions_by_path(
            stack,
            queue_path,
            [
                NagPackSuppression(
                    id="AwsSolutions-SQS4",
                    reason="Queue uses SQS managed encryption (SSE-SQS) and IAM-only access; SSL enforcement via aws:SecureTransport is defense-in-depth not required for internal-only queues",
                ),
            ],
        )


def _suppress_lambda_iam(stack: Stack) -> None:
    """Suppress Lambda/IAM findings on the shared execution role."""
    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Compute/LambdaExecRole/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason="AWSLambdaBasicExecutionRole is the standard AWS managed policy for Lambda CloudWatch Logs access",
                applies_to=[
                    "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                ],
            ),
        ],
    )

    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Compute/LambdaExecRole/DefaultPolicy/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-IAM5",
                reason="Wildcard resources required: S3 Vectors has no resource-level IAM support; Rekognition celebrity/face operations have no resource ARNs; Bedrock ListAsyncInvokes has no resource-level support; DynamoDB index ARN uses wildcard for GSI names; S3 object operations require /* suffix; OpenSearch HTTP actions require /* suffix",
                applies_to=[
                    "Resource::*",
                    "Resource::<StorageVideosBucket661BFADD.Arn>/*",
                    "Action::s3:GetBucket*",
                    "Action::s3:GetObject*",
                    "Action::s3:List*",
                ],
            ),
        ],
    )

    # StepFunctionsPolicy is a separate IAM Policy construct (not on DefaultPolicy)
    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Compute/StepFunctionsPolicy/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-IAM5",
                reason="Step Functions StartExecution is scoped to the specific state machine ARN",
            ),
        ],
    )


def _suppress_opensearch(stack: Stack) -> None:
    """Suppress OpenSearch findings for dev/demo deployment."""
    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Search/SegmentsDomain/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-OS1",
                reason="Dev/demo deployment — VPC placement adds significant complexity and cost without proportional security benefit for non-production data",
            ),
            NagPackSuppression(
                id="AwsSolutions-OS3",
                reason="Dev/demo deployment — access policies restrict to specific IAM role ARN; IP-based condition not needed for IAM-authenticated access",
            ),
            NagPackSuppression(
                id="AwsSolutions-OS4",
                reason="Single-node dev deployment — dedicated master nodes are for production multi-node clusters",
            ),
            NagPackSuppression(
                id="AwsSolutions-OS7",
                reason="Single-node dev deployment — zone awareness requires multiple data nodes",
            ),
            NagPackSuppression(
                id="AwsSolutions-OS9",
                reason="Slow logs not needed for dev/demo — application-level logging is sufficient for debugging",
            ),
        ],
    )


def _suppress_api_gateway(stack: Stack) -> None:
    """Suppress API Gateway findings."""
    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Api/Api/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-APIG2",
                reason="Request validation is performed in Lambda function code with detailed error responses — API Gateway request validators cannot express the domain-specific validation logic needed",
            ),
        ],
    )

    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Api",
        [
            NagPackSuppression(
                id="AwsSolutions-APIG4",
                reason="OPTIONS methods do not require authorization — they are CORS preflight requests that browsers send without credentials (standard behavior)",
            ),
            NagPackSuppression(
                id="AwsSolutions-COG4",
                reason="OPTIONS methods do not require Cognito authorizer — CORS preflight requests must be unauthenticated per the CORS specification",
            ),
        ],
        apply_to_children=True,
    )

    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Api/Api/DeploymentStage.prod/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-APIG3",
                reason="WAF is out of scope for this demo/dev deployment — API Gateway throttling and Cognito auth provide sufficient protection",
            ),
        ],
    )

    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Api/Api/CloudWatchRole/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason="AmazonAPIGatewayPushToCloudWatchLogs is the standard AWS managed policy required for API Gateway execution logging — CDK creates this automatically when logging is enabled",
                applies_to=[
                    "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs",
                ],
            ),
        ],
    )


def _suppress_cloudfront(stack: Stack) -> None:
    """Suppress CloudFront findings for both distributions."""
    for dist_path in [
        "/video-search-v2-stack/Cdn/StaticDistribution/Resource",
        "/video-search-v2-stack/Cdn/VideosDistribution/Resource",
    ]:
        NagSuppressions.add_resource_suppressions_by_path(
            stack,
            dist_path,
            [
                NagPackSuppression(
                    id="AwsSolutions-CFR1",
                    reason="No geo restriction — application is intended for global access",
                ),
                NagPackSuppression(
                    id="AwsSolutions-CFR2",
                    reason="WAF is out of scope for demo/dev deployment — CloudFront provides built-in DDoS protection via AWS Shield Standard",
                ),
                NagPackSuppression(
                    id="AwsSolutions-CFR3",
                    reason="CloudFront access logging not needed — S3 server access logs on origin buckets provide sufficient audit trail",
                ),
                NagPackSuppression(
                    id="AwsSolutions-CFR4",
                    reason="Using default CloudFront domain (*.cloudfront.net) with default CloudFront certificate — custom SSL certificate requires a custom domain",
                ),
            ],
        )


def _suppress_cognito(stack: Stack) -> None:
    """Suppress Cognito findings."""
    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Auth/UserPool/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-COG2",
                reason="MFA is set to OPTIONAL (not OFF) — users can enable TOTP MFA; mandatory MFA would add friction for a demo project",
            ),
            NagPackSuppression(
                id="AwsSolutions-COG3",
                reason="Advanced security features (Cognito threat protection) not enabled — adds per-MAU cost not justified for demo project",
            ),
        ],
    )


def _suppress_container(stack: Stack) -> None:
    """Suppress Fargate container findings for dev/demo deployment."""
    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Container/ProcessingVpc/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-VPC7",
                reason="VPC is used only for ephemeral Fargate tasks (shot segmentation) — no persistent workloads, no inbound traffic; flow logs add cost without proportional value for batch processing",
            ),
        ],
    )

    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Container/ProcessingCluster/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-ECS4",
                reason="Container Insights not needed for ephemeral batch tasks — CloudWatch Logs on the task provides sufficient observability",
            ),
        ],
    )

    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Container/TaskExecutionRole/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason="AmazonECSTaskExecutionRolePolicy is the standard AWS managed policy for ECS task execution (ECR pull + CloudWatch Logs)",
                applies_to=[
                    "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
                ],
            ),
        ],
    )

    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Container/SegmentationTask/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-ECS2",
                reason="S3_VIDEO_BUCKET is a non-sensitive bucket name — using SSM Parameter Store for a public bucket name adds unnecessary complexity",
            ),
        ],
    )


def _suppress_step_functions(stack: Stack) -> None:
    """Suppress Step Functions findings."""
    NagSuppressions.add_resource_suppressions_by_path(
        stack,
        "/video-search-v2-stack/Processing/VideoProcessing/Resource",
        [
            NagPackSuppression(
                id="AwsSolutions-SF1",
                reason="ERROR-level logging is configured — ALL-level logging would be excessively verbose for video processing pipelines that handle large binary payloads",
            ),
        ],
    )
