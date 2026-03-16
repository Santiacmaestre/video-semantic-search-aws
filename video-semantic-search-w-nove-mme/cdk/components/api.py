from constructs import Construct
from aws_cdk import (
    aws_apigateway as apigw,
    aws_cognito as cognito,
    aws_lambda as lambda_,
    aws_logs as logs,
)


class ApiConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        project_name: str,
        user_pool: cognito.IUserPool,
        search_fn: lambda_.IFunction,
        upload_fn: lambda_.IFunction,
        video_fn: lambda_.IFunction,
        entity_fn: lambda_.IFunction,
        project_fn: lambda_.IFunction,
        cloudfront_static_domain: str = "",
    ) -> None:
        super().__init__(scope, id)

        cors_origin = f"https://{cloudfront_static_domain}" if cloudfront_static_domain else "*"

        # Access log group
        access_log_group = logs.LogGroup(
            self, "ApiAccessLogs",
            retention=logs.RetentionDays.ONE_WEEK,
        )

        self.api = apigw.RestApi(
            self, "Api",

            description="Video Search API with Cognito authentication",
            endpoint_types=[apigw.EndpointType.REGIONAL],
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=[cors_origin],
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["Content-Type", "Authorization"],
            ),
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                logging_level=apigw.MethodLoggingLevel.INFO,
                data_trace_enabled=False,
                access_log_destination=apigw.LogGroupLogDestination(access_log_group),
                access_log_format=apigw.AccessLogFormat.custom(
                    '{"requestId":"$context.requestId",'
                    '"ip":"$context.identity.sourceIp",'
                    '"caller":"$context.identity.caller",'
                    '"user":"$context.identity.user",'
                    '"requestTime":"$context.requestTime",'
                    '"httpMethod":"$context.httpMethod",'
                    '"resourcePath":"$context.resourcePath",'
                    '"status":"$context.status",'
                    '"protocol":"$context.protocol",'
                    '"responseLength":"$context.responseLength"}'
                ),
            ),
        )

        # Cognito Authorizer
        authorizer = apigw.CognitoUserPoolsAuthorizer(
            self, "CognitoAuthorizer",
            authorizer_name=f"{project_name}-cognito-authorizer",
            cognito_user_pools=[user_pool],
        )

        # --- Gateway error responses for CORS ---
        for response_type in [apigw.ResponseType.DEFAULT_4_XX, apigw.ResponseType.DEFAULT_5_XX]:
            self.api.add_gateway_response(
                f"GatewayResponse{response_type.response_type}",
                type=response_type,
                response_headers={
                    "Access-Control-Allow-Origin": f"'{cors_origin}'",
                    "Access-Control-Allow-Headers": "'Content-Type,Authorization'",
                    "Access-Control-Allow-Methods": "'GET,POST,PUT,DELETE,OPTIONS'",
                },
            )

        # --- Helper ---
        def _add_method(resource: apigw.IResource, method: str, fn: lambda_.IFunction) -> apigw.Method:
            return resource.add_method(
                method,
                apigw.LambdaIntegration(fn),
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=authorizer,
            )

        # --- Routes ---

        api_resource = self.api.root.add_resource("api")

        # /api/search
        search = api_resource.add_resource("search")
        search_method = _add_method(search, "POST", search_fn)

        # /api/upload
        upload = api_resource.add_resource("upload")
        upload_method = _add_method(upload, "POST", upload_fn)

        # /api/videos
        videos = api_resource.add_resource("videos")
        _add_method(videos, "GET", video_fn)

        # /api/videos/{video_id}
        video_id = videos.add_resource("{video_id}")
        _add_method(video_id, "GET", video_fn)
        _add_method(video_id, "DELETE", video_fn)

        # /api/projects
        projects = api_resource.add_resource("projects")
        _add_method(projects, "GET", project_fn)
        _add_method(projects, "POST", project_fn)

        # /api/projects/{project_id}
        project_id = projects.add_resource("{project_id}")
        _add_method(project_id, "GET", project_fn)
        _add_method(project_id, "PUT", project_fn)
        _add_method(project_id, "DELETE", project_fn)

        # /api/entities
        entities = api_resource.add_resource("entities")
        _add_method(entities, "GET", entity_fn)
        _add_method(entities, "POST", entity_fn)

        # /api/entities/merge
        entities_merge = entities.add_resource("merge")
        _add_method(entities_merge, "POST", entity_fn)

        # /api/entities/manual
        entities_manual = entities.add_resource("manual")
        _add_method(entities_manual, "POST", entity_fn)

        # /api/entities/{entity_id}
        entity_id = entities.add_resource("{entity_id}")
        _add_method(entity_id, "DELETE", entity_fn)
        _add_method(entity_id, "PUT", entity_fn)

        # /api/entities/{entity_id}/appearances
        appearances = entity_id.add_resource("appearances")
        _add_method(appearances, "GET", entity_fn)

        # /api/entities/{entity_id}/thumbnail
        thumbnail = entity_id.add_resource("thumbnail")
        _add_method(thumbnail, "PUT", entity_fn)

        # --- Usage plan with per-method throttling ---
        plan = self.api.add_usage_plan(
            "UsagePlan",

            throttle=apigw.ThrottleSettings(
                rate_limit=50,
                burst_limit=100,
            ),
            quota=apigw.QuotaSettings(
                limit=10000,
                period=apigw.Period.DAY,
            ),
        )
        plan.add_api_stage(
            stage=self.api.deployment_stage,
            throttle=[
                # Search invokes Bedrock + OpenSearch — limit to prevent cost spikes
                apigw.ThrottlingPerMethod(
                    method=search_method,
                    throttle=apigw.ThrottleSettings(rate_limit=10, burst_limit=20),
                ),
                # Upload triggers Step Functions pipeline — limit to prevent runaway processing
                apigw.ThrottlingPerMethod(
                    method=upload_method,
                    throttle=apigw.ThrottleSettings(rate_limit=5, burst_limit=10),
                ),
            ],
        )

        self.api_endpoint = self.api.url
