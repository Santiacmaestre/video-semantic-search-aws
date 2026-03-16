from constructs import Construct
from aws_cdk import (
    Duration,
    Stack,
    Aws,
    CfnOutput,
    aws_cognito as cognito,
    aws_iam as iam,
    aws_s3 as s3,
)
from components.storage import StorageConstruct
from components.auth import AuthConstruct
from components.cdn import CdnConstruct
from components.compute import ComputeConstruct
from components.search import SearchConstruct
from components.processing import ProcessingConstruct
from components.api import ApiConstruct
from components.monitoring import MonitoringConstruct
from components.frontend_deployment import FrontendDeploymentConstruct


class VideoSearchStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        project_name: str,
        environment: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        account_id = Aws.ACCOUNT_ID
        region = Aws.REGION
        deploy_id = self.node.try_get_context("deploy_id") or ""

        # 1. Storage (no deps)
        storage = StorageConstruct(self, "Storage", project_name=project_name, account_id=account_id)

        # 2. Auth (no deps)
        auth = AuthConstruct(self, "Auth", project_name=project_name, account_id=account_id)

        # 3. CDN (needs storage buckets)
        cdn = CdnConstruct(
            self, "Cdn",
            project_name=project_name,
            static_bucket=storage.static_bucket,
            videos_bucket=storage.videos_bucket,
        )

        # 4. Videos bucket CORS with CloudFront domain
        videos_cfn_bucket = storage.videos_bucket.node.default_child
        videos_cfn_bucket.add_property_override("CorsConfiguration", {
            "CorsRules": [{
                "AllowedHeaders": ["*"],
                "AllowedMethods": ["GET", "PUT", "POST"],
                "AllowedOrigins": [f"https://{cdn.static_domain_name}"],
                "ExposedHeaders": ["ETag"],
                "MaxAge": 3600,
            }],
        })

        # 5. Bedrock bucket policy on videos bucket
        storage.videos_bucket.add_to_resource_policy(iam.PolicyStatement(
            sid="BedrockAccess",
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal("bedrock.amazonaws.com")],
            actions=["s3:GetObject", "s3:PutObject"],
            resources=[f"{storage.videos_bucket.bucket_arn}/*"],
        ))

        # 6. Cognito client with CloudFront callback URLs
        user_pool_client = auth.user_pool.add_client(
            "AppClient",
            user_pool_client_name=f"{project_name}-client",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(
                user_srp=True,
                user_password=True,
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(implicit_code_grant=True),
                scopes=[
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=[f"https://{cdn.static_domain_name}"],
                logout_urls=[f"https://{cdn.static_domain_name}"],
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
        )

        # Add ALLOW_USER_AUTH flow via L1 escape hatch (required for ESSENTIALS tier sign-in)
        cfn_client = user_pool_client.node.default_child
        cfn_client.add_property_override("ExplicitAuthFlows", [
            "ALLOW_USER_SRP_AUTH",
            "ALLOW_USER_PASSWORD_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
            "ALLOW_USER_AUTH",
        ])

        # 7. Compute (create IAM role early for OpenSearch master user)
        compute = ComputeConstruct(
            self, "Compute",
            project_name=project_name,
            account_id=account_id,
            videos_bucket=storage.videos_bucket,
            static_bucket=storage.static_bucket,
            videos_table=storage.videos_table,
            segments_table=storage.segments_table,
            entities_table=storage.entities_table,
            projects_table=storage.projects_table,
            processing_queue=storage.processing_queue,
            vector_bucket_name=storage.vector_bucket_name,
        )

        # 8. Search (needs lambda role)
        search = SearchConstruct(
            self, "Search",
            account_id=account_id,
            lambda_role=compute.lambda_role,
        )

        # 9. Wire OpenSearch endpoint back to Lambda env vars + permissions
        compute.add_opensearch_permissions(search.domain.domain_arn)
        compute.set_opensearch_endpoint(search.domain_endpoint)

        # 10. Processing (needs Lambda functions)
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

        # 11. Wire state machine ARN + permissions to orchestrator
        compute.add_state_machine_permissions(processing.state_machine.state_machine_arn)
        compute.set_state_machine_arn(processing.state_machine.state_machine_arn)

        # 12. API (needs Lambda functions + Cognito)
        api = ApiConstruct(
            self, "Api",
            project_name=project_name,
            user_pool=auth.user_pool,
            search_fn=compute.search_fn,
            upload_fn=compute.upload_fn,
            video_fn=compute.video_fn,
            entity_fn=compute.entity_fn,
            project_fn=compute.project_fn,
            cloudfront_static_domain=cdn.static_domain_name,
        )

        # 13. Monitoring
        MonitoringConstruct(self, "Monitoring", project_name=project_name, api=api.api)

        # 14. Frontend Deployment
        FrontendDeploymentConstruct(
            self, "FrontendDeployment",
            static_bucket=storage.static_bucket,
            static_distribution=cdn.static_distribution,
            api_endpoint=api.api_endpoint,
            cognito_domain=auth.user_pool_domain.domain_name,
            cognito_client_id=user_pool_client.user_pool_client_id,
            cloudfront_static_domain=cdn.static_domain_name,
            video_cdn_domain=cdn.videos_domain_name,
            region=region,
        )

        # --- Outputs ---
        CfnOutput(self, "ApiEndpoint", value=api.api_endpoint, description="API Gateway endpoint URL")
        CfnOutput(self, "CognitoUserPoolId", value=auth.user_pool.user_pool_id, description="Cognito User Pool ID")
        CfnOutput(self, "CognitoClientId", value=user_pool_client.user_pool_client_id, description="Cognito App Client ID")
        CfnOutput(self, "CognitoDomain", value=auth.user_pool_domain.domain_name, description="Cognito hosted UI domain")
        CfnOutput(self, "StaticWebsiteBucket", value=storage.static_bucket.bucket_name, description="S3 bucket for static website")
        CfnOutput(self, "VideoBucket", value=storage.videos_bucket.bucket_name, description="S3 bucket for videos")
        CfnOutput(self, "CloudFrontStaticDomain", value=cdn.static_domain_name, description="CloudFront domain for static website")
        CfnOutput(self, "CloudFrontVideoDomain", value=cdn.videos_domain_name, description="CloudFront domain for videos")
        CfnOutput(self, "VectorBucketName", value=storage.vector_bucket_name, description="S3 Vectors bucket name")
        CfnOutput(self, "StateMachineArn", value=processing.state_machine.state_machine_arn, description="Step Functions state machine ARN")
        CfnOutput(self, "OpenSearchEndpoint", value=search.domain_endpoint, description="OpenSearch domain endpoint")
        CfnOutput(self, "AppUrl", value=f"https://{cdn.static_domain_name}", description="Application URL")
