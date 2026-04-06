from constructs import Construct
from aws_cdk import (
    Duration,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_lambda as lambda_,
    aws_logs as logs,
)


class ProcessingConstruct(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        project_name: str,
        cluster: ecs.ICluster,
        task_definition: ecs.FargateTaskDefinition,
        container: ecs.ContainerDefinition,
        subnets: ec2.SubnetSelection,
        security_group: ec2.ISecurityGroup,
        read_result_fn: lambda_.IFunction,
        embedding_fn: lambda_.IFunction,
        transcription_fn: lambda_.IFunction,
        celebrity_detection_fn: lambda_.IFunction,
        caption_fn: lambda_.IFunction,
        merge_fn: lambda_.IFunction,
    ) -> None:
        super().__init__(scope, id)

        # --- State definitions ---

        # Fargate shot segmentation (replaces Lambda)
        shot_segmentation = tasks.EcsRunTask(
            self, "ShotSegmentation",
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            cluster=cluster,
            task_definition=task_definition,
            launch_target=tasks.EcsFargateLaunchTarget(
                platform_version=ecs.FargatePlatformVersion.LATEST,
            ),
            container_overrides=[
                tasks.ContainerOverride(
                    container_definition=container,
                    environment=[
                        tasks.TaskEnvironmentVariable(
                            name="TASK_INPUT",
                            value=sfn.JsonPath.json_to_string(
                                sfn.JsonPath.object_at("$")
                            ),
                        ),
                    ],
                ),
            ],
            subnets=subnets,
            security_groups=[security_group],
            assign_public_ip=True,
            result_path=sfn.JsonPath.DISCARD,
            timeout=Duration.seconds(21600),
        )
        shot_segmentation.add_retry(
            errors=["States.ALL"],
            interval=Duration.seconds(10),
            max_attempts=2,
            backoff_rate=2,
        )

        # Bridge: read Fargate result from S3
        read_segmentation_result = tasks.LambdaInvoke(
            self, "ReadSegmentationResult",
            lambda_function=read_result_fn,
            result_path="$.shot_result",
            payload_response_only=True,
        )

        prepare_parallel = sfn.Pass(
            self, "PrepareParallel",
            parameters={
                "video_id.$": "$.video_id",
                "filename.$": "$.filename",
                "s3_uri.$": "$.s3_uri",
                "project_id.$": "$.project_id",
                "embedding_model.$": "$.embedding_model",
                "metadata_model.$": "$.metadata_model",
                "segment_duration.$": "$.segment_duration",
                "segments_s3_key.$": "$.shot_result.segments_s3_key",
            },
        )

        # Branch 1: Embeddings
        embeddings = tasks.LambdaInvoke(
            self, "Embeddings",
            lambda_function=embedding_fn,
            result_path="$.result",
            payload_response_only=True,
            retry_on_service_exceptions=False,
        )
        embeddings.add_retry(
            errors=["States.ALL"],
            interval=Duration.seconds(10),
            max_attempts=2,
            backoff_rate=2,
        )

        # Branch 2: Transcription with fallback
        transcription_failed = sfn.Pass(
            self, "TranscriptionFailed",
            result=sfn.Result.from_object({"transcripts": [], "error": "Transcription failed"}),
            result_path="$.result",
        )

        transcription = tasks.LambdaInvoke(
            self, "Transcription",
            lambda_function=transcription_fn,
            result_path="$.result",
            payload_response_only=True,
            retry_on_service_exceptions=False,
        )
        transcription.add_retry(
            errors=["States.ALL"],
            interval=Duration.seconds(10),
            max_attempts=1,
            backoff_rate=2,
        )
        transcription.add_catch(transcription_failed, errors=["States.ALL"], result_path="$.error")

        # Branch 3: Celebrity Detection with fallback
        celebrity_failed = sfn.Pass(
            self, "CelebrityDetectionFailed",
            result=sfn.Result.from_object({"celebrities": []}),
            result_path="$.result",
        )

        celebrity_detection = tasks.LambdaInvoke(
            self, "CelebrityDetection",
            lambda_function=celebrity_detection_fn,
            result_path="$.result",
            payload_response_only=True,
            timeout=Duration.seconds(600),
            retry_on_service_exceptions=False,
        )
        celebrity_detection.add_retry(
            errors=["States.ALL"],
            interval=Duration.seconds(10),
            max_attempts=1,
            backoff_rate=2,
        )
        celebrity_detection.add_catch(celebrity_failed, errors=["States.ALL"], result_path="$.error")

        # Parallel processing
        parallel = sfn.Parallel(
            self, "ParallelProcessing",
            result_path="$.parallel_results",
        )
        parallel.branch(embeddings)
        parallel.branch(transcription)
        parallel.branch(celebrity_detection)

        # Prepare caption input
        prepare_caption = sfn.Pass(
            self, "PrepareCaptionInput",
            parameters={
                "video_id.$": "$.video_id",
                "metadata_model.$": "$.metadata_model",
                "transcription_result.$": "$.parallel_results[1].result",
            },
            result_path="$.caption_input",
        )

        # Prepare merge
        prepare_merge = sfn.Pass(
            self, "PrepareMerge",
            parameters={
                "video_id.$": "$.video_id",
                "project_id.$": "$.project_id",
                "embedding_model.$": "$.embedding_model",
                "metadata_model.$": "$.metadata_model",
                "embedding_result.$": "$.parallel_results[0].result",
                "transcription_result.$": "$.parallel_results[1].result",
                "celebrity_result.$": "$.parallel_results[2].result",
                "caption_result.$": "$.caption_result",
            },
        )

        # Generate captions
        generate_captions = tasks.LambdaInvoke(
            self, "GenerateCaptions",
            lambda_function=caption_fn,
            input_path="$.caption_input",
            result_path="$.caption_result",
            payload_response_only=True,
            timeout=Duration.seconds(900),
            retry_on_service_exceptions=False,
        )
        generate_captions.add_retry(
            errors=["States.ALL"],
            interval=Duration.seconds(10),
            max_attempts=1,
            backoff_rate=2,
        )
        generate_captions.add_catch(prepare_merge, errors=["States.ALL"], result_path="$.caption_result")

        # Mark failed
        mark_failed = tasks.LambdaInvoke(
            self, "MarkFailed",
            lambda_function=merge_fn,
            payload=sfn.TaskInput.from_object({
                "video_id.$": "$.video_id",
                "project_id.$": "$.project_id",
                "embedding_model.$": "$.embedding_model",
                "mark_failed": True,
            }),
            payload_response_only=True,
        )

        # Merge
        merge = tasks.LambdaInvoke(
            self, "Merge",
            lambda_function=merge_fn,
            payload_response_only=True,
            retry_on_service_exceptions=False,
        )
        merge.add_retry(
            errors=["States.ALL"],
            interval=Duration.seconds(5),
            max_attempts=2,
            backoff_rate=2,
        )
        merge.add_catch(mark_failed, errors=["States.ALL"], result_path="$.merge_error")

        # --- Chain ---
        definition = (
            shot_segmentation
            .next(read_segmentation_result)
            .next(prepare_parallel)
            .next(parallel)
            .next(prepare_caption)
            .next(generate_captions)
            .next(prepare_merge)
            .next(merge)
        )

        # Log group
        log_group = logs.LogGroup(
            self, "SfnLogGroup",
            retention=logs.RetentionDays.TWO_WEEKS,
        )

        self.state_machine = sfn.StateMachine(
            self, "VideoProcessing",

            definition_body=sfn.DefinitionBody.from_chainable(definition),
            tracing_enabled=True,
            logs=sfn.LogOptions(
                destination=log_group,
                level=sfn.LogLevel.ERROR,
                include_execution_data=True,
            ),
        )
