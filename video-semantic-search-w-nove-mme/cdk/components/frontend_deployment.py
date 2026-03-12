from constructs import Construct
from aws_cdk import (
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_cloudfront as cloudfront,
)


class FrontendDeploymentConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        static_bucket: s3.IBucket,
        static_distribution: cloudfront.IDistribution,
        api_endpoint: str,
        cognito_domain: str,
        cognito_client_id: str,
        cloudfront_static_domain: str,
        video_cdn_domain: str,
        region: str,
    ) -> None:
        super().__init__(scope, id)

        config_js = (
            "const CONFIG = {\n"
            f"    API_ENDPOINT: '{api_endpoint}api',\n"
            f"    COGNITO_DOMAIN: '{cognito_domain}.auth.{region}.amazoncognito.com',\n"
            f"    COGNITO_CLIENT_ID: '{cognito_client_id}',\n"
            f"    COGNITO_REDIRECT_URI: 'https://{cloudfront_static_domain}',\n"
            f"    VIDEO_CDN: 'https://{video_cdn_domain}'\n"
            "};\n"
        )

        s3deploy.BucketDeployment(
            self, "DeployFrontend",
            sources=[
                s3deploy.Source.asset("../frontend-static", exclude=["js/config.js"]),
                s3deploy.Source.data("js/config.js", config_js),
            ],
            destination_bucket=static_bucket,
            distribution=static_distribution,
            distribution_paths=["/*"],
        )
