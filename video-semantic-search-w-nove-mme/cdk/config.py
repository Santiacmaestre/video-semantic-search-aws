from aws_cdk import Duration
from aws_cdk.aws_lambda import Runtime

PROJECT_NAME = "video-search-v2"
LAMBDA_RUNTIME = Runtime.PYTHON_3_13
DEFAULT_LAMBDA_MEMORY = 512
DEFAULT_LAMBDA_TIMEOUT = Duration.seconds(30)
# --- Bedrock models ---
# Amazon Nova only, per the approved provider list (Titan, Nova, Mistral,
# Llama 3/3.2, DeepSeek).
#
# The `us.` prefix is a cross-region inference profile: one request fans out
# across us-east-1, us-east-2 and us-west-2, and each leg authorizes against the
# region it lands in. All three are on the approved region list, so these work.
# `global.*` is NOT usable — it can route outside those three and gets denied.
#
# Nova 2 text models are inference-profile-only (no ON_DEMAND), which is why
# they carry the `us.` prefix while the embedding model does not. Check with:
#   aws bedrock get-foundation-model --model-identifier <id> --region us-east-1

# Embeddings for video/audio/text (Nova Multimodal Embeddings).
# ON_DEMAND, and offered in us-east-1 only — this pins the stack's region.
NOVA_MODEL_ID = "amazon.nova-2-multimodal-embeddings-v1:0"
# Segment captions + genre classification. Needs VIDEO input support.
NOVA_LITE_MODEL_ID = "us.amazon.nova-2-lite-v1:0"
# Query weight analysis on the search path (Bedrock Converse API). Text only.
ANALYZER_MODEL_ID = "us.amazon.nova-2-lite-v1:0"
NOVA_ANALYZER_MODEL_ID = None  # Set via CDK context: -c nova_analyzer_model_id=arn:...
