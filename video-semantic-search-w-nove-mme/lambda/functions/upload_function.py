"""Lambda function for generating presigned S3 URLs for video upload."""
import json
import os
import uuid
import boto3
from datetime import datetime

s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

S3_VIDEO_BUCKET = os.environ['S3_VIDEO_BUCKET']
VIDEOS_TABLE = os.environ['VIDEOS_TABLE']
SQS_QUEUE_URL = os.environ['SQS_QUEUE_URL']


def lambda_handler(event, context):
    """Generate presigned URL for direct S3 upload."""
    try:
        # Parse request body
        body = json.loads(event.get('body', '{}'))
        filename = body.get('filename', '')
        content_type = body.get('contentType', 'video/mp4')
        
        if not filename:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'Filename is required'})
            }
        
        # Sanitize filename: replace spaces and special chars with underscores
        import re
        sanitized_filename = re.sub(r'[^\w\-.]', '_', filename)
        
        # Generate video ID
        video_id = str(uuid.uuid4())
        s3_key = f"uploads/{video_id}_{sanitized_filename}"
        
        # Generate presigned URL for upload (valid for 15 minutes)
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': S3_VIDEO_BUCKET,
                'Key': s3_key,
                'ContentType': content_type
            },
            ExpiresIn=900
        )
        
        # Store video metadata in DynamoDB with pending status
        table = dynamodb.Table(VIDEOS_TABLE)
        item = {
            'video_id': video_id,
            'filename': sanitized_filename,  # Store sanitized filename
            's3_uri': f"s3://{S3_VIDEO_BUCKET}/{s3_key}",
            'status': 'pending',
            'created_at': datetime.utcnow().isoformat(),
            'segments': []
        }
        # Add project_id if provided
        project_id = body.get('project_id', '')
        if project_id:
            item['project_id'] = project_id
        table.put_item(Item=item)
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'success': True,
                'video_id': video_id,
                'upload_url': presigned_url,
                's3_key': s3_key
            })
        }
        
    except Exception as e:
        print(f"Error generating presigned URL: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }
