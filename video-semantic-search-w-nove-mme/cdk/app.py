#!/usr/bin/env python3
import os
import aws_cdk as cdk
from stacks.video_search_stack import VideoSearchStack

app = cdk.App()

project_name = app.node.try_get_context("project_name") or "video-search-v2"
environment = app.node.try_get_context("environment") or "prod"

VideoSearchStack(
    app,
    f"{project_name}-stack",
    project_name=project_name,
    environment=environment,
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region="us-east-1",
    ),
)

app.synth()
