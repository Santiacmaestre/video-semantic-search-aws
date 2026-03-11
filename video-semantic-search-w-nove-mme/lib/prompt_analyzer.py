"""Multi-model query weight analyzer using Bedrock Converse API."""
import boto3
import json
import os
from typing import Dict
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = lambda: None


bedrock_client = boto3.client('bedrock-runtime', region_name=os.getenv('AWS_REGION'))
DEFAULT_MODEL_ID = os.getenv('NOVA_ANALYZER_MODEL_ID') or os.getenv('CLAUDE_MODEL_ID')

_weight_cache = {}

SYSTEM_MESSAGE = """Analyze video search queries and assign weights (0.0-1.0) for four modalities.
Weights must sum to 1.0.

Return ONLY valid JSON in this exact format:
{
  "visual": 0.0,
  "audio": 0.0,
  "transcription": 0.0,
  "metadata": 0.0,
  "reasoning": "brief explanation"
}

Guidelines:
- visual: For appearance, colors, objects, actions, scenes, people's looks
- audio: For sounds, music, noise, non-speech audio
- transcription: For spoken words, dialogue, narration, text content
- metadata: For searching by person name, genre, captions, keywords, factual attributes

Examples:
- "red car driving" → {"visual": 0.9, "audio": 0.0, "transcription": 0.0, "metadata": 0.1, "reasoning": "Primarily visual with slight metadata for caption match"}
- "person saying hello" → {"visual": 0.2, "audio": 0.2, "transcription": 0.5, "metadata": 0.1, "reasoning": "Focus on speech content"}
- "Cristiano Ronaldo" → {"visual": 0.3, "audio": 0.0, "transcription": 0.1, "metadata": 0.6, "reasoning": "Person name best matched via metadata"}
- "sports highlights" → {"visual": 0.3, "audio": 0.1, "transcription": 0.1, "metadata": 0.5, "reasoning": "Genre and caption keywords important"}
- "dog barking loudly" → {"visual": 0.3, "audio": 0.6, "transcription": 0.0, "metadata": 0.1, "reasoning": "Primarily audio-focused"}"""


def analyze_query_weights(query_text: str, analyzer_model_id: str = None) -> Dict:
    """Analyze query and assign modality weights using Bedrock Converse API."""
    cache_key = f"{analyzer_model_id or 'default'}:{query_text}"
    if cache_key in _weight_cache:
        print(f"Using cached weights for: {query_text}")
        return _weight_cache[cache_key]

    model_id = analyzer_model_id or DEFAULT_MODEL_ID

    def try_parse_weights(attempt=1):
        try:
            response = bedrock_client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": query_text}]}],
                system=[{"text": SYSTEM_MESSAGE}],
                inferenceConfig={"maxTokens": 200, "temperature": 0.0}
            )

            content = response["output"]["message"]["content"][0]["text"]

            start = content.find('{')
            end = content.rfind('}') + 1
            if start >= 0 and end > start:
                weights_data = json.loads(content[start:end])

                visual = float(weights_data.get('visual', 0))
                audio = float(weights_data.get('audio', 0))
                transcription = float(weights_data.get('transcription', 0))
                metadata = float(weights_data.get('metadata', 0))
                reasoning = weights_data.get('reasoning', '')

                total = visual + audio + transcription + metadata
                if abs(total - 1.0) > 0.01:
                    raise ValueError(f"Weights sum to {total}, not 1.0")

                result = {
                    'visual': visual,
                    'audio': audio,
                    'transcription': transcription,
                    'metadata': metadata,
                    'reasoning': reasoning
                }

                _weight_cache[cache_key] = result
                print(f"Weights ({model_id}): visual={visual:.2f}, audio={audio:.2f}, transcription={transcription:.2f}, metadata={metadata:.2f}")
                print(f"Reasoning: {reasoning}")
                return result
            else:
                raise ValueError("No JSON found in response")

        except Exception as e:
            if attempt < 2:
                print(f"Retry {attempt}: {e}")
                return try_parse_weights(attempt + 1)
            else:
                print(f"Failed to get weights from {model_id}, using fallback: {e}")
                return {
                    'visual': 0.4,
                    'audio': 0.2,
                    'transcription': 0.2,
                    'metadata': 0.2,
                    'reasoning': 'Default weights (analysis failed)'
                }

    return try_parse_weights()
