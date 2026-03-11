"""Search API Lambda — project-scoped hybrid search."""
import json
import os
import sys
import boto3

sys.path.insert(0, '/opt/python')

from search_engine import search_with_fusion

dynamodb = boto3.resource('dynamodb')
PROJECTS_TABLE = os.environ.get('PROJECTS_TABLE', 'video-search-v2-projects')


def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
        query = body.get('query', '')
        qparams = event.get('queryStringParameters', {}) or {}
        project_id = qparams.get('project_id', '')

        if not query:
            return respond(400, {'error': 'Query parameter is required'})

        # Get analyzer model from project config
        analyzer_model_id = os.environ.get('CLAUDE_MODEL_ID')  # default: Haiku
        if project_id:
            try:
                proj = dynamodb.Table(PROJECTS_TABLE).get_item(Key={'project_id': project_id}).get('Item', {})
                custom_id = proj.get('analyzer_model_id')
                if custom_id:
                    analyzer_model_id = custom_id
            except Exception:
                pass

        results = search_with_fusion(query, project_id=project_id, analyzer_model_id=analyzer_model_id)
        results['analyzer_model_id'] = analyzer_model_id
        return respond(200, results)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return respond(500, {'error': str(e)})


def respond(status, body):
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
        'body': json.dumps(body, default=str),
    }
