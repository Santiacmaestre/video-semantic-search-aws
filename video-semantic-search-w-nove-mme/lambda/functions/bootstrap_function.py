"""Bootstrap custom resource — creates demo user, project, and uploads sample video."""
import json
import os
import sys
import uuid
import urllib.request
from datetime import datetime

sys.path.insert(0, '/opt/python')

import boto3

cognito = boto3.client('cognito-idp', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))

DEMO_EMAIL = 'demo@workshop.com'
DEMO_PASSWORD = 'Demo1234!'
MERIDIAN_URL = 'https://ws-assets-prod-iad-r-pdx-f3b3f9f1a7d6a3d0.s3.us-west-2.amazonaws.com/7db2455e-0fa6-4f6d-9973-84daccd6421f/Netflix_Open_Content_Meridian.mp4'
MERIDIAN_FILENAME = 'Netflix_Open_Content_Meridian.mp4'


def lambda_handler(event, context):
    """CloudFormation custom resource handler."""
    import cfnresponse
    try:
        request_type = event.get('RequestType', '')
        physical_id = event.get('PhysicalResourceId', 'bootstrap-demo')

        if request_type == 'Create':
            result = _create(event)
            cfnresponse.send(event, context, cfnresponse.SUCCESS, result, physical_id)

        elif request_type == 'Delete':
            _delete(event)
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {}, physical_id)

        else:
            # Update — no-op
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {}, physical_id)

    except Exception as e:
        print(f"Bootstrap error: {e}")
        import traceback
        traceback.print_exc()
        cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': str(e)}, physical_id)


def _create(event):
    """Create demo user, project, and trigger video ingestion."""
    props = event.get('ResourceProperties', {})
    user_pool_id = props['UserPoolId']
    projects_table = props['ProjectsTable']
    videos_table = props['VideosTable']
    video_bucket = props['VideoBucket']
    vector_bucket = props['VectorBucket']

    # 1. Create Cognito user
    try:
        cognito.admin_create_user(
            UserPoolId=user_pool_id,
            Username=DEMO_EMAIL,
            UserAttributes=[
                {'Name': 'email', 'Value': DEMO_EMAIL},
                {'Name': 'email_verified', 'Value': 'true'},
            ],
            MessageAction='SUPPRESS',
        )
    except cognito.exceptions.UsernameExistsException:
        print(f"User {DEMO_EMAIL} already exists, skipping creation")

    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=DEMO_EMAIL,
        Password=DEMO_PASSWORD,
        Permanent=True,
    )

    # Get user's sub (user_id)
    user_resp = cognito.admin_get_user(UserPoolId=user_pool_id, Username=DEMO_EMAIL)
    user_id = next(a['Value'] for a in user_resp['UserAttributes'] if a['Name'] == 'sub')
    print(f"Demo user created: {DEMO_EMAIL} (sub={user_id})")

    # 2. Create project
    project_id = str(uuid.uuid4())[:8]
    proj_table = dynamodb.Table(projects_table)
    proj_table.put_item(Item={
        'project_id': project_id,
        'user_id': user_id,
        'name': 'Meridian Demo',
        'analyzer_model': os.environ.get('CLAUDE_MODEL_ID', 'global.anthropic.claude-haiku-4-5-20251001-v1:0'),
        'segment_duration': 10,
        'metadata_model': 'nova-lite',
        'vector_engine': 'opensearch',
        'created_at': datetime.utcnow().isoformat(),
    })
    print(f"Project created: {project_id}")

    # 3. Create per-project indices
    os.environ['S3_VECTOR_BUCKET'] = vector_bucket
    from vector_store import create_project_indices
    create_project_indices(project_id)
    print(f"S3 Vector indices created for {project_id}")

    from opensearch_client import create_index
    create_index(project_id, 'opensearch')
    print(f"OpenSearch index created: segments-{project_id}")

    # 4. Create video record
    video_id = str(uuid.uuid4())
    s3_key = f"uploads/{video_id}_{MERIDIAN_FILENAME}"
    vid_table = dynamodb.Table(videos_table)
    vid_table.put_item(Item={
        'video_id': video_id,
        'filename': MERIDIAN_FILENAME,
        's3_uri': f"s3://{video_bucket}/{s3_key}",
        'status': 'pending',
        'created_at': datetime.utcnow().isoformat(),
        'segments': [],
        'project_id': project_id,
    })
    print(f"Video record created: {video_id}")

    # 5. Download Meridian video and upload to S3
    print(f"Downloading {MERIDIAN_URL} ...")
    tmp_path = '/tmp/meridian.mp4'
    urllib.request.urlretrieve(MERIDIAN_URL, tmp_path)
    file_size = os.path.getsize(tmp_path)
    print(f"Downloaded {file_size / 1024 / 1024:.1f} MB, uploading to s3://{video_bucket}/{s3_key}")

    s3.upload_file(tmp_path, video_bucket, s3_key, ExtraArgs={'ContentType': 'video/mp4'})
    os.remove(tmp_path)
    print(f"Upload complete — pipeline will trigger via S3 event notification")

    return {
        'DemoEmail': DEMO_EMAIL,
        'DemoPassword': DEMO_PASSWORD,
        'ProjectId': project_id,
        'VideoId': video_id,
    }


def _delete(event):
    """Clean up Cognito user on stack deletion."""
    props = event.get('ResourceProperties', {})
    user_pool_id = props['UserPoolId']
    try:
        cognito.admin_delete_user(UserPoolId=user_pool_id, Username=DEMO_EMAIL)
        print(f"Deleted demo user: {DEMO_EMAIL}")
    except Exception as e:
        print(f"Could not delete demo user (may not exist): {e}")
