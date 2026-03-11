"""Lambda function for video operations (list, get, delete) - project scoped."""
import json
import os
import boto3
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
VIDEOS_TABLE = os.environ['VIDEOS_TABLE']
CORS = {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def respond(status, body):
    return {'statusCode': status, 'headers': CORS, 'body': json.dumps(body, cls=DecimalEncoder)}


def get_project_id(event):
    params = event.get('queryStringParameters', {}) or {}
    return params.get('project_id', '')


def lambda_handler(event, context):
    try:
        method = event.get('httpMethod', 'GET')
        path_params = event.get('pathParameters', {}) or {}
        video_id = path_params.get('video_id')
        project_id = get_project_id(event)
        table = dynamodb.Table(VIDEOS_TABLE)

        if method == 'GET' and not video_id:
            if project_id:
                response = table.scan(
                    FilterExpression='project_id = :pid',
                    ExpressionAttributeValues={':pid': project_id}
                )
            else:
                response = table.scan()
            videos = sorted(response.get('Items', []), key=lambda x: x.get('created_at', ''), reverse=True)
            return respond(200, {'videos': videos})

        elif method == 'GET' and video_id:
            item = table.get_item(Key={'video_id': video_id}).get('Item')
            if not item:
                return respond(404, {'error': 'Video not found'})
            return respond(200, item)

        elif method == 'DELETE' and video_id:
            table.delete_item(Key={'video_id': video_id})
            return respond(200, {'success': True})

        return respond(400, {'error': 'Invalid request'})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return respond(500, {'error': str(e)})
