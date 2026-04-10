"""Generate per-segment captions and classify video genre using Nova Lite.

Each video clip is captioned individually with the actual video as input (not frames),
giving the LLM temporal context for actions and transitions. Captions serve dual purposes:
displayed in search results AND indexed in OpenSearch for BM25 text search.
"""
import boto3
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from botocore.exceptions import ClientError

bedrock = boto3.client('bedrock-runtime', region_name=os.getenv('AWS_REGION', 'us-east-1'))
s3 = boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-east-1'))

NOVA_LITE_MODEL = os.environ.get('NOVA_LITE_MODEL_ID', 'us.amazon.nova-2-lite-v1:0')

GENRES = [
    'Sports', 'News', 'Entertainment', 'Documentary', 'Education',
    'Music', 'Gaming', 'Cooking', 'Travel', 'Technology', 'Business',
    'Lifestyle', 'Sci-Fi', 'Mystery', 'Other',
]

CAPTION_PROMPT = """Describe this video clip in 3-5 sentences. Include:
- What is happening, who is visible, actions, setting, and environment
- Any text on screen: titles, subtitles, signs, logos, watermarks, or credits
- If the screen is mostly black or blank, state "Black frame" or "Blank screen"
- If showing opening/closing credits or title cards, describe them as such
{transcription}
Return ONLY the descriptive caption, nothing else."""

GENRE_PROMPT = f"""Based on all the video segments described below, classify the overall video into exactly ONE genre from this list: {', '.join(GENRES)}

Segment descriptions:
{{captions}}

Return ONLY the genre name, nothing else."""


def handler(event, context):
    """Caption all clips for a video and classify genre."""
    video_id = event.get('video_id', '')
    transcription_result = event.get('transcription_result', {})
    bucket = os.environ.get('S3_VIDEO_BUCKET', '')

    # List clips from S3
    clip_objs = s3.list_objects_v2(Bucket=bucket, Prefix=f"clips/{video_id}/").get('Contents', [])
    clip_keys = sorted([o['Key'] for o in clip_objs if o['Key'].endswith('.mp4')])

    # Load transcripts from S3 (avoids Step Functions payload limit)
    transcripts = transcription_result.get('transcripts', [])
    if transcription_result.get('transcripts_s3_key'):
        try:
            obj = s3.get_object(Bucket=bucket, Key=transcription_result['transcripts_s3_key'])
            transcripts = json.loads(obj['Body'].read())
        except Exception as e:
            print(f"Error loading transcripts from S3: {e}")
    tx_map = {t['segment_index']: t['text'] for t in transcripts}

    # Generate per-segment captions in parallel
    def _caption_segment(key):
        idx = int(key.split('/')[-1].replace('seg_', '').replace('.mp4', ''))
        clip_uri = f"s3://{bucket}/{key}"
        caption = _generate_caption(clip_uri, tx_map.get(idx, ''))
        return {'segment_index': idx, 'caption': caption}

    captions = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_caption_segment, key): key for key in clip_keys}
        for future in as_completed(futures):
            captions.append(future.result())
    captions.sort(key=lambda c: c['segment_index'])

    # Classify genre from all captions combined
    all_text = '\n'.join(f"- {c['caption']}" for c in captions if c['caption'])
    genre = _classify_genre(all_text) if all_text else 'Other'

    # Write captions to S3 (avoids Step Functions payload limit)
    captions_key = f"metadata/{video_id}/captions.json"
    s3.put_object(Bucket=bucket, Key=captions_key, Body=json.dumps(captions), ContentType='application/json')

    return {'captions_s3_key': captions_key, 'genre': genre, 'caption_count': len(captions)}


def _bedrock_invoke_with_retry(client, max_retries=3, **kwargs):
    """invoke_model with exponential backoff for throttling/transient errors."""
    for attempt in range(max_retries + 1):
        try:
            return client.invoke_model(**kwargs)
        except ClientError as e:
            code = e.response['Error']['Code']
            if code in ('ThrottlingException', 'TooManyRequestsException',
                        'ServiceUnavailableException', 'ModelTimeoutException') and attempt < max_retries:
                delay = min(2 ** attempt + random.uniform(0, 1), 30)
                print(f"Bedrock retry {attempt+1}/{max_retries} after {delay:.1f}s: {code}")
                time.sleep(delay)
            else:
                raise


def _generate_caption(clip_s3_uri, transcription=''):
    """Caption a single video clip using Nova Lite with optional transcription context."""
    tx_hint = f'\nTranscription: "{transcription}"' if transcription else ''
    prompt = CAPTION_PROMPT.format(transcription=tx_hint)

    try:
        resp = _bedrock_invoke_with_retry(bedrock,
            modelId=NOVA_LITE_MODEL,
            body=json.dumps({
                'messages': [{'role': 'user', 'content': [
                    {'video': {'format': 'mp4', 'source': {'s3Location': {'uri': clip_s3_uri}}}},
                    {'text': prompt},
                ]}],
                'inferenceConfig': {'maxTokens': 500, 'temperature': 0.3},
            }),
            accept='application/json',
            contentType='application/json',
        )
        return json.loads(resp['body'].read())['output']['message']['content'][0]['text'].strip()
    except Exception as e:
        print(f"Caption error: {e}")
        return ''


def _classify_genre(captions_text):
    """Classify video genre from combined segment captions."""
    try:
        resp = _bedrock_invoke_with_retry(bedrock,
            modelId=NOVA_LITE_MODEL,
            body=json.dumps({
                'messages': [{'role': 'user', 'content': [{'text': GENRE_PROMPT.format(captions=captions_text)}]}],
                'inferenceConfig': {'maxTokens': 20, 'temperature': 0.1},
            }),
            accept='application/json',
            contentType='application/json',
        )
        genre = json.loads(resp['body'].read())['output']['message']['content'][0]['text'].strip()
        return genre if genre in GENRES else 'Other'
    except Exception as e:
        print(f"Genre error: {e}")
        return 'Other'
