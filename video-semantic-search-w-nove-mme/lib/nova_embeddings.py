"""Amazon Nova Multimodal Embeddings module."""
import boto3
import json
import os
import time
from typing import List, Dict
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

aws_region = os.getenv('AWS_REGION', 'us-east-1')
bedrock_runtime = boto3.client('bedrock-runtime', region_name=aws_region)
s3_client = boto3.client('s3', region_name=aws_region)

NOVA_MODEL_ID = 'amazon.nova-2-multimodal-embeddings-v1:0'
NOVA_DIMENSION = 1024
SEGMENT_DURATION = 10


def generate_video_embeddings_nova(s3_uri: str, segment_duration: int = SEGMENT_DURATION) -> List[Dict]:
    """Generate video + audio embeddings using Nova MME async API."""
    output_bucket = os.getenv('S3_VIDEO_BUCKET')
    
    # Filenames are now sanitized (no spaces), so use URI as-is
    print(f"S3 URI for Bedrock: {s3_uri}")
    
    # Detect video format from file extension
    file_ext = s3_uri.lower().split('.')[-1]
    format_map = {
        'mp4': 'mp4',
        'mov': 'mov',
        'mkv': 'mkv',
        'webm': 'webm',
        'flv': 'flv',
        'mpeg': 'mpeg',
        'mpg': 'mpg',
        'wmv': 'wmv',
        '3gp': '3gp'
    }
    video_format = format_map.get(file_ext, 'mp4')
    print(f"Detected video format: {video_format}")

    model_input = {
        'taskType': 'SEGMENTED_EMBEDDING',
        'segmentedEmbeddingParams': {
            'embeddingPurpose': 'GENERIC_INDEX',
            'embeddingDimension': NOVA_DIMENSION,
            'video': {
                'format': video_format,
                'embeddingMode': 'AUDIO_VIDEO_SEPARATE',
                'source': {'s3Location': {'uri': s3_uri}},
                'segmentationConfig': {'durationSeconds': segment_duration}
            }
        }
    }

    response = bedrock_runtime.start_async_invoke(
        modelId=NOVA_MODEL_ID,
        modelInput=model_input,
        outputDataConfig={'s3OutputDataConfig': {'s3Uri': f's3://{output_bucket}/nova-embeddings/'}}
    )

    invocation_arn = response['invocationArn']
    print(f"Nova async started: {invocation_arn}")

    # Poll for completion
    while True:
        status_response = bedrock_runtime.get_async_invoke(invocationArn=invocation_arn)
        status = status_response['status']
        if status == 'Completed':
            break
        elif status == 'Failed':
            raise Exception(f"Nova embedding failed: {status_response.get('failureMessage', 'Unknown')}")
        time.sleep(5)

    print("Nova embedding completed")

    # Parse output from S3
    output_uri = status_response['outputDataConfig']['s3OutputDataConfig']['s3Uri']
    bucket = output_uri.replace('s3://', '').split('/')[0]
    prefix = '/'.join(output_uri.replace('s3://', '').split('/')[1:])

    # Read video embeddings
    video_embs = _read_jsonl(bucket, f"{prefix}/embedding-video.jsonl")
    audio_embs = _read_jsonl(bucket, f"{prefix}/embedding-audio.jsonl")

    # Build segments
    segments = []
    for i, v_emb in enumerate(video_embs):
        start_sec = i * segment_duration
        end_sec = start_sec + segment_duration
        seg = {
            'segment_index': i,
            'start_sec': start_sec,
            'end_sec': end_sec,
            'visual_emb': v_emb.get('embedding', []),
        }
        # Match audio embedding by index
        if i < len(audio_embs):
            seg['audio_emb'] = audio_embs[i].get('embedding', [])
        segments.append(seg)

    print(f"Generated {len(segments)} Nova segments (video+audio)")
    return segments


def generate_text_embedding_nova(text: str, purpose: str = 'GENERIC_RETRIEVAL') -> List[float]:
    """Generate text embedding using Nova MME sync API."""
    request_body = {
        'taskType': 'SINGLE_EMBEDDING',
        'singleEmbeddingParams': {
            'embeddingPurpose': purpose,
            'embeddingDimension': NOVA_DIMENSION,
            'text': {'truncationMode': 'END', 'value': text}
        }
    }

    response = bedrock_runtime.invoke_model(
        body=json.dumps(request_body),
        modelId=NOVA_MODEL_ID,
        accept='application/json',
        contentType='application/json'
    )

    result = json.loads(response['body'].read())
    return result['embeddings'][0]['embedding']


def generate_image_embedding_nova(s3_uri: str, purpose: str = 'GENERIC_INDEX') -> List[float]:
    """Generate image embedding using Nova MME sync API (for face crops)."""
    # Download image from S3
    bucket = s3_uri.replace('s3://', '').split('/')[0]
    key = '/'.join(s3_uri.replace('s3://', '').split('/')[1:])
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    image_bytes = obj['Body'].read()

    # Detect image format from magic bytes
    import base64
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        img_format = 'png'
    elif image_bytes[:2] == b'\xff\xd8':
        img_format = 'jpeg'
    elif image_bytes[:4] == b'GIF8':
        img_format = 'gif'
    elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        img_format = 'webp'
    else:
        img_format = 'jpeg'
    image_b64 = base64.b64encode(image_bytes).decode('utf-8')

    request_body = {
        'taskType': 'SINGLE_EMBEDDING',
        'singleEmbeddingParams': {
            'embeddingPurpose': purpose,
            'embeddingDimension': NOVA_DIMENSION,
            'image': {'format': img_format, 'source': {'bytes': image_b64}}
        }
    }

    response = bedrock_runtime.invoke_model(
        body=json.dumps(request_body),
        modelId=NOVA_MODEL_ID,
        accept='application/json',
        contentType='application/json'
    )

    result = json.loads(response['body'].read())
    return result['embeddings'][0]['embedding']


def _read_jsonl(bucket: str, key: str) -> List[Dict]:
    """Read JSONL file from S3."""
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        content = obj['Body'].read().decode('utf-8')
        return [json.loads(line) for line in content.strip().split('\n') if line.strip()]
    except Exception as e:
        print(f"Warning: Could not read {key}: {e}")
        return []


def transcribe_video(s3_uri: str, segment_duration: int = SEGMENT_DURATION, overlap: float = 5.0) -> List[Dict]:
    """Transcribe video using AWS Transcribe, return per-segment text aligned to video segments."""
    import uuid
    transcribe = boto3.client('transcribe', region_name=aws_region)

    job_name = f"nova-transcribe-{uuid.uuid4().hex[:12]}"
    bucket = s3_uri.replace('s3://', '').split('/')[0]
    
    transcribe.start_transcription_job(
        TranscriptionJobName=job_name,
        Media={'MediaFileUri': s3_uri},
        MediaFormat='mp4',
        LanguageCode='en-US',
        OutputBucketName=bucket,
        OutputKey=f'transcripts/{job_name}.json'
    )

    print(f"Transcribe job started: {job_name}")

    # Wait for completion
    while True:
        status = transcribe.get_transcription_job(TranscriptionJobName=job_name)
        job_status = status['TranscriptionJob']['TranscriptionJobStatus']
        if job_status == 'COMPLETED':
            break
        elif job_status == 'FAILED':
            raise Exception(f"Transcribe failed: {status['TranscriptionJob'].get('FailureReason', '')}")
        time.sleep(3)

    # Read transcript from S3
    obj = s3_client.get_object(Bucket=bucket, Key=f'transcripts/{job_name}.json')
    transcript_data = json.loads(obj['Body'].read().decode())

    # Extract word-level timestamps
    words = []
    for item in transcript_data.get('results', {}).get('items', []):
        if item['type'] == 'pronunciation':
            words.append({
                'word': item['alternatives'][0]['content'],
                'start': float(item['start_time']),
                'end': float(item['end_time'])
            })
        elif item['type'] == 'punctuation':
            if words:
                words[-1]['word'] += item['alternatives'][0]['content']

    # Group words into segments with overlap
    segments = []
    seg_idx = 0
    seg_start = 0.0

    while seg_start < (words[-1]['end'] if words else 0):
        seg_end = seg_start + segment_duration
        # Include words in [seg_start - overlap, seg_end + overlap]
        seg_words = [w for w in words if w['end'] >= (seg_start - overlap) and w['start'] <= (seg_end + overlap)]
        text = ' '.join(w['word'] for w in seg_words)

        if text.strip():
            segments.append({
                'segment_index': seg_idx,
                'start_sec': seg_start,
                'end_sec': seg_end,
                'text': text
            })
        seg_idx += 1
        seg_start = seg_end

    # Cleanup
    try:
        transcribe.delete_transcription_job(TranscriptionJobName=job_name)
        s3_client.delete_object(Bucket=bucket, Key=f'transcripts/{job_name}.json')
    except:
        pass

    print(f"Transcribed {len(segments)} segments with {len(words)} words")
    return segments
