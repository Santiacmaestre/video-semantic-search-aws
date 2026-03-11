"""Orchestrator Lambda — triggered by S3 upload, starts Step Functions execution."""
import json
import os
import time
import boto3
from urllib.parse import unquote_plus

aws_region = os.environ.get('AWS_REGION', 'us-east-1')
dynamodb = boto3.resource('dynamodb', region_name=aws_region)
sfn_client = boto3.client('stepfunctions', region_name=aws_region)

VIDEOS_TABLE = os.environ.get('VIDEOS_TABLE', '')
PROJECTS_TABLE = os.environ.get('PROJECTS_TABLE', '')
STATE_MACHINE_ARN = os.environ.get('STATE_MACHINE_ARN', '')


def lambda_handler(event, context):
    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = unquote_plus(record['s3']['object']['key'])

        if not key.startswith('uploads/'):
            continue

        filename_part = key.replace('uploads/', '')
        video_id = filename_part.split('_', 1)[0]

        vid_table = dynamodb.Table(VIDEOS_TABLE)
        video = vid_table.get_item(Key={'video_id': video_id}).get('Item')
        if not video:
            continue

        if video.get('status') in ('processing', 'completed'):
            continue

        s3_uri = f"s3://{bucket}/{key}"
        project_id = video.get('project_id', '')

        # Get project config
        segment_duration = 10
        metadata_model = 'nova-lite'
        if project_id:
            try:
                proj = dynamodb.Table(PROJECTS_TABLE).get_item(Key={'project_id': project_id}).get('Item', {})
                segment_duration = int(proj.get('segment_duration', 10))
                metadata_model = proj.get('metadata_model', 'nova-lite')
            except Exception as e:
                print(f"Error reading project config: {e}")

        vid_table.update_item(
            Key={'video_id': video_id},
            UpdateExpression='SET #status = :s',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':s': 'processing'},
        )

        sfn_input = {
            'video_id': video_id,
            'filename': video.get('filename', ''),
            's3_uri': s3_uri,
            'project_id': project_id,
            'embedding_model': 'nova-mme',
            'metadata_model': metadata_model,
            'segment_duration': segment_duration,
        }

        response = sfn_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=f"video-{video_id}-{int(time.time())}",
            input=json.dumps(sfn_input),
        )
        print(f"Started execution: {response['executionArn']}")

    return {'statusCode': 200}
