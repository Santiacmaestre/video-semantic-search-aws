from aws_cdk import Duration
from aws_cdk.aws_lambda import Runtime

PROJECT_NAME = "video-search-v2"
LAMBDA_RUNTIME = Runtime.PYTHON_3_13
DEFAULT_LAMBDA_MEMORY = 512
DEFAULT_LAMBDA_TIMEOUT = Duration.seconds(30)
NOVA_MODEL_ID = "amazon.nova-2-multimodal-embeddings-v1:0"
NOVA_LITE_MODEL_ID = "amazon.nova-lite-v1:0"
CLAUDE_MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
NOVA_ANALYZER_MODEL_ID = None  # Set via CDK context: -c nova_analyzer_model_id=arn:...
