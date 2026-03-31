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

    def grant_state_machine(self, state_machine_role: iam.IRole) -> None:
        """Grant Step Functions role permission to run and monitor ECS tasks."""
        self.task_definition.grant_run(state_machine_role)
