"""Bridge Lambda — reads Fargate segmentation result from S3 and returns it."""
import json
import os
import boto3

s3_client = boto3.client('s3')
S3_VIDEO_BUCKET = os.environ.get('S3_VIDEO_BUCKET', '')


def lambda_handler(event, context):
    video_id = event['video_id']
    result_key = f"processing/{video_id}/segmentation_result.json"

    resp = s3_client.get_object(Bucket=S3_VIDEO_BUCKET, Key=result_key)
    result = json.loads(resp['Body'].read())

    print(f"Read segmentation result: {len(result['segments'])} segments, {result['video_duration']}s")
    return {
        'segments_s3_key': result_key,
        'segment_count': len(result['segments']),
        'video_duration': result['video_duration'],
    }
