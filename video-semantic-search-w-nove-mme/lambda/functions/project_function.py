"""Project CRUD — create, read, update, delete projects with per-project resources."""
import json
import os
import sys
import uuid
import boto3
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, '/opt/python')

dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
PROJECTS_TABLE = os.environ['PROJECTS_TABLE']
VIDEOS_TABLE = os.environ['VIDEOS_TABLE']
SEGMENTS_TABLE = os.environ['SEGMENTS_TABLE']
ENTITIES_TABLE = os.environ['ENTITIES_TABLE']

CORS = {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (bytes, bytearray)):
            return None
        return super().default(obj)


def respond(status, body):
    return {'statusCode': status, 'headers': CORS, 'body': json.dumps(body, cls=DecimalEncoder)}


def get_user_id(event):
    """Extract user_id from Cognito JWT via API Gateway context."""
    claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
    return claims.get('sub', claims.get('cognito:username', ''))


def lambda_handler(event, context):
    try:
        method = event.get('httpMethod', 'GET')
        params = event.get('pathParameters', {}) or {}
        project_id = params.get('project_id')
        user_id = get_user_id(event)
        table = dynamodb.Table(PROJECTS_TABLE)

        # GET /api/projects — list user's projects
        if method == 'GET' and not project_id:
            resp = table.query(
                IndexName='user_id-index',
                KeyConditionExpression='user_id = :uid',
                ExpressionAttributeValues={':uid': user_id},
            )
            projects = resp.get('Items', [])
            vid_table = dynamodb.Table(VIDEOS_TABLE)
            for p in projects:
                p['video_count'] = vid_table.scan(
                    FilterExpression='project_id = :pid',
                    ExpressionAttributeValues={':pid': p['project_id']},
                    Select='COUNT',
                ).get('Count', 0)
            return respond(200, {'projects': sorted(projects, key=lambda x: x.get('created_at', ''), reverse=True)})

        # POST /api/projects — create
        if method == 'POST' and not project_id:
            body = json.loads(event.get('body', '{}'))
            pid = str(uuid.uuid4())
            item = {
                'project_id': pid,
                'user_id': user_id,
                'name': body.get('name', 'Untitled'),
                'analyzer_model': body.get('analyzer_model', 'nova-micro'),
                'segment_duration': int(body.get('segment_duration', 10)),
                'metadata_model': body.get('metadata_model', 'nova-lite'),
                'vector_engine': body.get('vector_engine', 's3_vectors'),
                'created_at': datetime.utcnow().isoformat(),
            }
            table.put_item(Item=item)

            # Create per-project S3 Vector indices
            try:
                from vector_store import create_project_indices
                create_project_indices(pid)
            except Exception as e:
                print(f"Warning: Could not create vector indices: {e}")

            # Create OpenSearch index with chosen vector engine
            try:
                from opensearch_client import create_index
                create_index(pid, item['vector_engine'])
            except Exception as e:
                print(f"Warning: Could not create OpenSearch index: {e}")

            return respond(201, {'success': True, 'project': item})

        # GET /api/projects/{id}
        if method == 'GET' and project_id:
            item = table.get_item(Key={'project_id': project_id}).get('Item')
            if not item:
                return respond(404, {'error': 'Project not found'})
            return respond(200, item)

        # PUT /api/projects/{id}
        if method == 'PUT' and project_id:
            body = json.loads(event.get('body', '{}'))
            update_parts, values, names = [], {}, {}
            for field in ['name', 'analyzer_model', 'metadata_model', 'segment_duration']:
                if field in body:
                    safe = field.replace('_', '')
                    update_parts.append(f'#{safe} = :{safe}')
                    names[f'#{safe}'] = field
                    val = body[field]
                    if field == 'segment_duration' and isinstance(val, (int, float)):
                        val = Decimal(str(val))
                    values[f':{safe}'] = val
            if update_parts:
                table.update_item(
                    Key={'project_id': project_id},
                    UpdateExpression='SET ' + ', '.join(update_parts),
                    ExpressionAttributeNames=names,
                    ExpressionAttributeValues=values,
                )
            return respond(200, {'success': True})

        # DELETE /api/projects/{id}
        if method == 'DELETE' and project_id:
            # Delete S3 Vector indices
            try:
                from vector_store import delete_project_indices
                delete_project_indices(project_id)
            except Exception as e:
                print(f"Warning: Could not delete vector indices: {e}")

            # Delete S3 files
            bucket = os.environ.get('S3_VIDEO_BUCKET', '')
            if bucket:
                vid_tbl = dynamodb.Table(VIDEOS_TABLE)
                vid_resp = vid_tbl.scan(FilterExpression='project_id = :pid', ExpressionAttributeValues={':pid': project_id})
                for vid in vid_resp.get('Items', []):
                    try:
                        resp = s3_client.list_objects_v2(Bucket=bucket, Prefix=f"uploads/{vid['video_id']}_")
                        for obj in resp.get('Contents', []):
                            s3_client.delete_object(Bucket=bucket, Key=obj['Key'])
                    except Exception:
                        pass

            # Delete DynamoDB records
            for tbl_name, key_name in [(VIDEOS_TABLE, 'video_id'), (ENTITIES_TABLE, 'entity_id')]:
                tbl = dynamodb.Table(tbl_name)
                resp = tbl.scan(FilterExpression='project_id = :pid', ExpressionAttributeValues={':pid': project_id})
                for item in resp.get('Items', []):
                    tbl.delete_item(Key={key_name: item[key_name]})

            for tbl_name, keys in [(SEGMENTS_TABLE, ['video_id', 'segment_id'])]:
                tbl = dynamodb.Table(tbl_name)
                resp = tbl.scan(FilterExpression='project_id = :pid', ExpressionAttributeValues={':pid': project_id})
                for item in resp.get('Items', []):
                    tbl.delete_item(Key={k: item[k] for k in keys})

            table.delete_item(Key={'project_id': project_id})

            # Delete OpenSearch index
            try:
                from opensearch_client import delete_index
                delete_index(project_id)
            except Exception as e:
                print(f"Warning: Could not delete OpenSearch index: {e}")

            return respond(200, {'success': True})

        return respond(400, {'error': 'Invalid request'})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return respond(500, {'error': str(e)})
