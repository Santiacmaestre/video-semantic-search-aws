"""Merge Lambda — combines outputs from all parallel pipeline branches.

Stores segment metadata in DynamoDB, retrieves vectors from S3 Vectors,
builds OpenSearch documents with captions/people/genre/vectors, and bulk-indexes them.
"""
import json
import os
import sys
sys.path.insert(0, '/opt/python')

import boto3
from datetime import datetime

aws_region = os.environ.get('AWS_REGION', 'us-east-1')
dynamodb = boto3.resource('dynamodb', region_name=aws_region)
s3vectors = boto3.client('s3vectors', region_name=aws_region)
s3 = boto3.client('s3', region_name=aws_region)

VIDEOS_TABLE = os.environ.get('VIDEOS_TABLE', '')
SEGMENTS_TABLE = os.environ.get('SEGMENTS_TABLE', '')
S3_VECTOR_BUCKET = os.environ.get('S3_VECTOR_BUCKET', '')
S3_VIDEO_BUCKET = os.environ.get('S3_VIDEO_BUCKET', '')

DIMENSION = 1024


def lambda_handler(event, context):
    """Merge parallel outputs and index everything to OpenSearch."""
    if event.get('backfill_titles'):
        return _backfill_titles(event.get('project_id', ''))

    video_id = event['video_id']
    project_id = event.get('project_id', '')

    # Read project config for vector engine
    vector_engine = 's3_vectors'
    if project_id:
        try:
            proj = dynamodb.Table(os.environ.get('PROJECTS_TABLE', '')).get_item(Key={'project_id': project_id}).get('Item', {})
            vector_engine = proj.get('vector_engine', 's3_vectors')
        except Exception:
            pass

    embedding_result = event.get('embedding_result', {})
    transcription_result = event.get('transcription_result', {})
    celebrity_result = event.get('celebrity_result', {})
    caption_result = event.get('caption_result', {})

    segments = embedding_result.get('segments', [])
    celebrities = celebrity_result.get('celebrities', [])
    genre = caption_result.get('genre', '')

    # Load captions from S3 (avoids Step Functions payload limit)
    captions = []
    if caption_result.get('captions_s3_key'):
        try:
            obj = s3.get_object(Bucket=S3_VIDEO_BUCKET, Key=caption_result['captions_s3_key'])
            captions = json.loads(obj['Body'].read())
        except Exception as e:
            print(f"Error loading captions from S3: {e}")

    transcripts = transcription_result.get('transcripts', [])

    # Store segment metadata in DynamoDB
    seg_table = dynamodb.Table(SEGMENTS_TABLE)
    for seg in segments:
        seg_table.put_item(Item={
            'video_id': video_id,
            'segment_id': f"seg_{seg['segment_index']:04d}",
            'segment_index': seg['segment_index'],
            'start_sec': str(seg['start_sec']),
            'end_sec': str(seg['end_sec']),
            'has_visual': seg.get('has_visual', False),
            'has_audio': seg.get('has_audio', False),
            'project_id': project_id,
        })

    # Update video status
    dynamodb.Table(VIDEOS_TABLE).update_item(
        Key={'video_id': video_id},
        UpdateExpression='SET #s = :s, segment_count = :sc, genre = :g, completed_at = :ca',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={
            ':s': 'completed',
            ':sc': len(segments),
            ':g': genre,
            ':ca': datetime.utcnow().isoformat(),
        },
    )

    # Index to OpenSearch
    filename = dynamodb.Table(VIDEOS_TABLE).get_item(Key={'video_id': video_id}).get('Item', {}).get('filename', '')
    if segments:
        try:
            _index_to_opensearch(video_id, project_id, segments, captions, transcripts, celebrities, genre, vector_engine, filename)
        except Exception as e:
            print(f"OpenSearch indexing failed: {e}")

    return {'status': 'completed', 'segment_count': len(segments)}


def _index_to_opensearch(video_id, project_id, segments, captions, transcripts, celebrities, genre, vector_engine='s3_vectors', filename=''):
    """Build OpenSearch documents with vectors + metadata and bulk-index them."""
    from opensearch_client import bulk_index_segments
    from vector_store import get_indices

    caption_map = {c['segment_index']: c['caption'] for c in captions}
    tx_map = {t['segment_index']: t['text'] for t in transcripts}

    # Map celebrities to segments by timestamp overlap
    celeb_by_seg = {}
    for celeb in celebrities:
        for ts in celeb.get('timestamps', []):
            for seg in segments:
                if seg['start_sec'] <= ts <= seg['end_sec']:
                    celeb_by_seg.setdefault(seg['segment_index'], set()).add(celeb['name'])
                    break

    # Retrieve vectors from S3 Vectors (batch in chunks of 100)
    indices = get_indices(project_id)
    vectors_by_seg = {}
    for modality in ['visual', 'audio', 'transcription']:
        keys = [f"{video_id}_seg{seg['segment_index']:04d}_{modality}" for seg in segments]
        try:
            for i in range(0, len(keys), 100):
                resp = s3vectors.get_vectors(
                    vectorBucketName=S3_VECTOR_BUCKET,
                    indexName=indices[modality],
                    keys=keys[i:i + 100],
                    returnData=True,
                )
                for v in resp.get('vectors', []):
                    seg_part = v['key'].rsplit('_', 1)[0].rsplit('_', 1)[1]
                    idx = int(seg_part.replace('seg', ''))
                    vectors_by_seg.setdefault(idx, {})[f'{modality}_vector'] = v['data']['float32']
        except Exception as e:
            print(f"Could not retrieve {modality} vectors: {e}")

    # Build documents
    today = datetime.utcnow().strftime('%Y-%m-%d')
    title = os.path.splitext(filename)[0].replace('_', ' ') if filename else ''
    docs = []
    for seg in segments:
        idx = seg['segment_index']
        vecs = vectors_by_seg.get(idx, {})
        doc = {
            'video_id': video_id,
            'segment_id': f"seg_{idx:04d}",
            'title': title,
            'caption': caption_map.get(idx, ''),
            'people': sorted(celeb_by_seg.get(idx, set())),
            'genre': genre,
            'upload_date': today,
            'start_sec': seg['start_sec'],
            'end_sec': seg['end_sec'],
        }
        for field in ['visual_vector', 'audio_vector', 'transcription_vector']:
            if field in vecs:
                doc[field] = vecs[field]
        docs.append(doc)

    bulk_index_segments(project_id, docs, DIMENSION, vector_engine)


def _backfill_titles(project_id):
    """Backfill title field in OpenSearch from DynamoDB video filenames."""
    from opensearchpy import OpenSearch
    from requests_aws4auth import AWS4Auth

    endpoint = os.environ.get('OPENSEARCH_ENDPOINT', '')
    host = endpoint.replace('https://', '')
    region = os.environ.get('AWS_REGION', 'us-east-1')
    creds = boto3.Session().get_credentials()
    auth = AWS4Auth(creds.access_key, creds.secret_key, region, 'es', session_token=creds.token)
    client = OpenSearch(hosts=[{'host': host, 'port': 443}], http_auth=auth,
                        use_ssl=True, verify_certs=True,
                        connection_class=__import__('opensearchpy').RequestsHttpConnection,
                        timeout=120)

    index_name = f"segments-{project_id}"

    resp = dynamodb.Table(VIDEOS_TABLE).scan(
        FilterExpression='project_id = :pid',
        ExpressionAttributeValues={':pid': project_id},
        ProjectionExpression='video_id, filename',
    )
    title_map = {}
    for item in resp.get('Items', []):
        filename = item.get('filename', '')
        title_map[item['video_id']] = os.path.splitext(filename)[0].replace('_', ' ') if filename else ''

    updated = 0
    for vid, title in title_map.items():
        if not title:
            continue
        body = {
            "query": {"term": {"video_id": vid}},
            "script": {"source": "ctx._source.title = params.title", "params": {"title": title}},
        }
        try:
            r = client.update_by_query(index=index_name, body=body, refresh=True)
            updated += r.get('updated', 0)
            print(f"Updated {vid}: {r.get('updated', 0)} docs -> \"{title}\"")
        except Exception as e:
            print(f"Error updating {vid}: {e}")

    return {'status': 'completed', 'updated': updated, 'titles': title_map}
