"""Generate video embeddings using Amazon Nova Multimodal Embeddings.

Processes each video clip through Nova MME's sync API to produce separate
visual and audio embeddings (1024d each), then stores them in S3 Vectors.
"""
import json
import os
import sys
sys.path.insert(0, '/opt/python')

import boto3

bedrock = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))

NOVA_MODEL_ID = os.environ.get('NOVA_MODEL_ID', 'amazon.nova-2-multimodal-embeddings-v1:0')
NOVA_DIMENSION = 1024


def lambda_handler(event, context):
    """Embed all clips for a video and store vectors in S3 Vectors."""
    video_id = event['video_id']
    project_id = event.get('project_id', '')
    filename = event.get('filename', '')
    shot_segments = event.get('shot_segments', [])

    segments = []
    for seg in shot_segments:
        clip_uri = seg.get('clip_s3_uri', '')
        if not clip_uri:
            continue

        file_ext = clip_uri.lower().split('.')[-1]
        video_format = file_ext if file_ext in ('mp4', 'mov', 'mkv', 'webm') else 'mp4'

        try:
            result = _embed_clip(clip_uri, video_format)
            segments.append({
                'segment_index': seg['segment_index'],
                'start_sec': seg['start_sec'],
                'end_sec': seg['end_sec'],
                'visual_emb': result.get('visual'),
                'audio_emb': result.get('audio'),
            })
        except Exception as e:
            print(f"Error embedding segment {seg['segment_index']}: {e}")

    # Store all embeddings in S3 Vectors
    from vector_store import store_video_embeddings
    store_video_embeddings(video_id, filename, segments, project_id)

    return {
        'segments': [
            {'segment_index': s['segment_index'], 'start_sec': s['start_sec'], 'end_sec': s['end_sec'],
             'has_visual': s.get('visual_emb') is not None,
             'has_audio': s.get('audio_emb') is not None}
            for s in segments
        ],
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
