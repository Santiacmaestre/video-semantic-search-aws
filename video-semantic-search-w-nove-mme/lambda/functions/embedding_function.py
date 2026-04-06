"""Generate video embeddings using Amazon Nova Multimodal Embeddings.

Processes each video clip through Nova MME's sync API to produce separate
visual and audio embeddings (1024d each), then stores them in S3 Vectors.
"""
import json
import os
import sys
sys.path.insert(0, '/opt/python')

from concurrent.futures import ThreadPoolExecutor, as_completed
import boto3

aws_region = os.environ.get('AWS_REGION', 'us-east-1')
bedrock = boto3.client('bedrock-runtime', region_name=aws_region)
s3 = boto3.client('s3', region_name=aws_region)

NOVA_MODEL_ID = os.environ.get('NOVA_MODEL_ID', 'amazon.nova-2-multimodal-embeddings-v1:0')
S3_VIDEO_BUCKET = os.environ.get('S3_VIDEO_BUCKET', '')
NOVA_DIMENSION = 1024


def lambda_handler(event, context):
    """Embed all clips for a video and store vectors in S3 Vectors."""
    video_id = event['video_id']
    project_id = event.get('project_id', '')
    filename = event.get('filename', '')

    # Load segments from S3 (avoids Step Functions payload limit)
    segments_s3_key = event.get('segments_s3_key', '')
    if segments_s3_key:
        obj = s3.get_object(Bucket=S3_VIDEO_BUCKET, Key=segments_s3_key)
        shot_segments = json.loads(obj['Body'].read()).get('segments', [])
    else:
        shot_segments = event.get('shot_segments', [])

    def _process_segment(seg):
        clip_uri = seg.get('clip_s3_uri', '')
        if not clip_uri:
            return None
        file_ext = clip_uri.lower().split('.')[-1]
        video_format = file_ext if file_ext in ('mp4', 'mov', 'mkv', 'webm') else 'mp4'
        try:
            result = _embed_clip(clip_uri, video_format)
            return {
                'segment_index': seg['segment_index'],
                'start_sec': seg['start_sec'],
                'end_sec': seg['end_sec'],
                'visual_emb': result.get('visual'),
                'audio_emb': result.get('audio'),
            }
        except Exception as e:
            print(f"Error embedding segment {seg['segment_index']}: {e}")
            return None

    segments = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_process_segment, seg): seg for seg in shot_segments}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                segments.append(result)
    segments.sort(key=lambda s: s['segment_index'])

    # Store all embeddings in S3 Vectors
    from vector_store import store_video_embeddings
    store_video_embeddings(video_id, filename, segments, project_id)

    # Write segment metadata to S3 (avoids Step Functions payload limit)
    segments_meta = [
        {'segment_index': s['segment_index'], 'start_sec': s['start_sec'], 'end_sec': s['end_sec'],
         'has_visual': s.get('visual_emb') is not None,
         'has_audio': s.get('audio_emb') is not None}
        for s in segments
    ]
    segments_key = f"metadata/{video_id}/embedding_segments.json"
    s3.put_object(Bucket=S3_VIDEO_BUCKET, Key=segments_key, Body=json.dumps(segments_meta), ContentType='application/json')

    return {
        'segments_s3_key': segments_key,
        'segment_count': len(segments),
    }


def _embed_clip(clip_uri, video_format='mp4'):
    """Call Nova MME sync API for a single clip, returning visual and audio embeddings."""
    response = bedrock.invoke_model(
        modelId=NOVA_MODEL_ID,
        body=json.dumps({
            'taskType': 'SINGLE_EMBEDDING',
            'singleEmbeddingParams': {
                'embeddingPurpose': 'GENERIC_INDEX',
                'embeddingDimension': NOVA_DIMENSION,
                'video': {
                    'format': video_format,
                    'embeddingMode': 'AUDIO_VIDEO_SEPARATE',
                    'source': {'s3Location': {'uri': clip_uri}},
                },
            },
        }),
        accept='application/json',
        contentType='application/json',
    )
    result = json.loads(response['body'].read())

    embeddings = {}
    for emb in result.get('embeddings', []):
        if emb['embeddingType'] == 'VIDEO':
            embeddings['visual'] = emb['embedding']
        elif emb['embeddingType'] == 'AUDIO':
            embeddings['audio'] = emb['embedding']
    return embeddings
