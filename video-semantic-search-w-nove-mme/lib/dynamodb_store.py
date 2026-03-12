"""DynamoDB table management for video metadata."""
import boto3
import os
from datetime import datetime
from typing import Dict, List
from decimal import Decimal

aws_region = os.getenv('AWS_REGION', 'us-east-1')
dynamodb = boto3.resource('dynamodb', region_name=aws_region)

VIDEOS_TABLE = os.environ.get('VIDEOS_TABLE', 'video-search-v2-videos')
SEGMENTS_TABLE = os.environ.get('SEGMENTS_TABLE', 'video-search-v2-segments')
ENTITIES_TABLE = os.environ.get('ENTITIES_TABLE', 'video-search-v2-entities')


def put_video(video_id: str, filename: str, s3_uri: str, status: str = 'pending'):
    """Store video metadata."""
    table = dynamodb.Table(VIDEOS_TABLE)
    table.put_item(Item={
        'video_id': video_id,
        'filename': filename,
        's3_uri': s3_uri,
        'status': status,
        'created_at': datetime.utcnow().isoformat(),
        'updated_at': datetime.utcnow().isoformat(),
    })


def put_segment(video_id: str, segment_index: int, start_sec: float, end_sec: float,
                has_visual: bool, has_audio: bool, has_transcription: bool):
    """Store segment metadata."""
    table = dynamodb.Table(SEGMENTS_TABLE)
    table.put_item(Item={
        'video_id': video_id,
        'segment_index': segment_index,
        'start_sec': Decimal(str(start_sec)),
        'end_sec': Decimal(str(end_sec)),
        'duration': Decimal(str(end_sec - start_sec)),
        'has_visual': has_visual,
        'has_audio': has_audio,
        'has_transcription': has_transcription,
        'created_at': datetime.utcnow().isoformat(),
    })


def get_video(video_id: str) -> Dict:
    """Get video metadata."""
    table = dynamodb.Table(VIDEOS_TABLE)
    return table.get_item(Key={'video_id': video_id}).get('Item')


def get_segments(video_id: str) -> List[Dict]:
    """Get all segments for a video."""
    table = dynamodb.Table(SEGMENTS_TABLE)
    response = table.query(
        KeyConditionExpression='video_id = :vid',
        ExpressionAttributeValues={':vid': video_id},
    )
    return response.get('Items', [])


def list_videos() -> List[Dict]:
    """List all videos."""
    table = dynamodb.Table(VIDEOS_TABLE)
    return table.scan().get('Items', [])


def update_video_status(video_id: str, status: str, segment_count: int = None):
    """Update video processing status."""
    table = dynamodb.Table(VIDEOS_TABLE)
    update_expr = 'SET #status = :status, updated_at = :updated'
    expr_values = {':status': status, ':updated': datetime.utcnow().isoformat()}

    if segment_count is not None:
        update_expr += ', segment_count = :count'
        expr_values[':count'] = segment_count

    table.update_item(
        Key={'video_id': video_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues=expr_values,
    )
