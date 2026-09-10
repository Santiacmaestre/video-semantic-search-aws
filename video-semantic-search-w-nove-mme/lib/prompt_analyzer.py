"""Multi-model query weight analyzer using Bedrock Converse API."""
import boto3
import json
import os
import random
import time
from typing import Dict
from botocore.exceptions import ClientError
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = lambda: None


bedrock_client = boto3.client('bedrock-runtime', region_name=os.getenv('AWS_REGION'))
DEFAULT_MODEL_ID = os.getenv('NOVA_ANALYZER_MODEL_ID') or os.getenv('ANALYZER_MODEL_ID')

_weight_cache = {}


def _bedrock_converse_with_retry(client, max_retries=3, **kwargs):
    """converse() with exponential backoff for throttling/transient errors."""
    for attempt in range(max_retries + 1):
        try:
            return client.converse(**kwargs)
        except ClientError as e:
            code = e.response['Error']['Code']
            if code in ('ThrottlingException', 'TooManyRequestsException',
                        'ServiceUnavailableException', 'ModelTimeoutException') and attempt < max_retries:
                delay = min(2 ** attempt + random.uniform(0, 1), 30)
                print(f"Bedrock retry {attempt+1}/{max_retries} after {delay:.1f}s: {code}")
                time.sleep(delay)
            else:
                raise


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

Modality definitions:
- visual: Appearance, colors, objects, actions, scenes, physical descriptions
- audio: Non-speech sounds, music, ambient noise, sound effects
- transcription: Spoken words, dialogue, narration, speech content
- metadata: Person names, video titles, genre, text on screen, captions, named entities, factual attributes

Weight assignment rules:
1. Focus on semantic intent, not surface-level word choice. Synonymous phrases describing the same action or concept must receive identical weights regardless of the specific words used.
2. When a query contains ANY proper noun, named entity, or distinctive/uncommon term, metadata should be 0.4. BM25 keyword matching is the only way to match names and titles. Remaining weight goes to the dominant content modality (usually visual).
3. Only assign audio weight when the query explicitly describes non-speech sounds, music, or acoustic qualities.
4. Assign transcription weight only when the query is about spoken words or dialogue content.

Weight patterns (follow these strictly):
- Pure visual, no names: "red car driving fast" → visual=1.0
- Visual + a name/title: "car chase in [name]" → metadata=0.5, visual=0.4, transcription=0.1
- Person doing something: "[Person] scores a goal" → metadata=0.3, visual=0.6, transcription=0.1
- Speech content: "talking about climate change" → transcription=0.5, visual=0.2, audio=0.1, metadata=0.2
- Sound-focused: "loud explosion sound" → audio=0.7, visual=0.3"""


def analyze_query_weights(query_text: str, analyzer_model_id: str = None) -> Dict:
    """Analyze query and assign modality weights using Bedrock Converse API."""
    cache_key = f"{analyzer_model_id or 'default'}:{query_text}"
    if cache_key in _weight_cache:
        print(f"Using cached weights for: {query_text}")
        return _weight_cache[cache_key]

    model_id = analyzer_model_id or DEFAULT_MODEL_ID

    def try_parse_weights(attempt=1):
        try:
            response = _bedrock_converse_with_retry(bedrock_client,
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
