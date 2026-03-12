from constructs import Construct
from aws_cdk import (
    Duration,
    aws_cloudwatch as cloudwatch,
    aws_apigateway as apigw,
)


class MonitoringConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        project_name: str,
        api: apigw.RestApi,
    ) -> None:
        super().__init__(scope, id)

        cloudwatch.Alarm(
            self, "LambdaErrorsAlarm",

            alarm_description="Alert when Lambda errors exceed threshold",
            metric=cloudwatch.Metric(
                namespace="AWS/Lambda",
                metric_name="Errors",
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=10,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        cloudwatch.Alarm(
            self, "Api5xxAlarm",

            alarm_description="Alert when API Gateway 5xx errors exceed threshold",
            metric=api.metric_server_error(
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=10,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
