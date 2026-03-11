"""Entity CRUD Lambda — named entities with image embeddings for @name visual search."""
import json
import os
import sys
import time
import boto3
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, '/opt/python')

dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')
ENTITIES_TABLE = os.environ['ENTITIES_TABLE']
S3_VIDEO_BUCKET = os.environ.get('S3_VIDEO_BUCKET', '')
S3_VECTOR_BUCKET = os.environ.get('S3_VECTOR_BUCKET', '')

CORS = {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def respond(status, body):
    return {'statusCode': status, 'headers': CORS, 'body': json.dumps(body, cls=DecimalEncoder)}


def lambda_handler(event, context):
    try:
        method = event.get('httpMethod', 'GET')
        params = event.get('pathParameters', {}) or {}
        entity_id = params.get('entity_id')
        qparams = event.get('queryStringParameters', {}) or {}
        project_id = qparams.get('project_id', '')

        table = dynamodb.Table(ENTITIES_TABLE)

        if method == 'GET' and not entity_id:
            return handle_list(table, project_id)
        if method == 'GET' and entity_id:
            item = table.get_item(Key={'entity_id': entity_id}).get('Item')
            return respond(200, item) if item else respond(404, {'error': 'Not found'})
        if method == 'POST':
            return handle_create(event, table, project_id)
        if method == 'PUT' and entity_id:
            return handle_update(event, entity_id, table)
        if method == 'DELETE' and entity_id:
            return handle_delete(entity_id, table, project_id)

        return respond(400, {'error': 'Invalid request'})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return respond(500, {'error': str(e)})


def handle_list(table, project_id=''):
    if project_id:
        response = table.scan(
            FilterExpression='project_id = :pid',
            ExpressionAttributeValues={':pid': project_id},
        )
    else:
        response = table.scan()

    entities = [{
        'entity_id': item['entity_id'],
        'name': item.get('name', ''),
        'description': item.get('description', ''),
        'has_embedding': item.get('has_embedding', False),
        'image_key': item.get('image_key', ''),
        'created_at': item.get('created_at', ''),
    } for item in response.get('Items', [])]

    return respond(200, {'entities': sorted(entities, key=lambda x: x.get('created_at', ''), reverse=True)})


def handle_create(event, table, project_id=''):
    body = json.loads(event.get('body', '{}'))
    name = body.get('name', '').strip()
    if not name:
        return respond(400, {'error': 'name is required'})

    entity_id = f"entity_{name.lower().replace(' ', '_')}_{int(time.time())}"
    image_key = f"entities/{project_id}/{entity_id}.jpg"

    # Generate presigned upload URL for the image
    upload_url = s3.generate_presigned_url('put_object', Params={
        'Bucket': S3_VIDEO_BUCKET, 'Key': image_key, 'ContentType': 'image/jpeg',
    }, ExpiresIn=300)

    entity = {
        'entity_id': entity_id,
        'name': name,
        'description': body.get('description', ''),
        'project_id': project_id,
        'image_key': image_key,
        'has_embedding': False,
        'created_at': datetime.utcnow().isoformat(),
    }
    table.put_item(Item=entity)

    return respond(201, {'entity_id': entity_id, 'upload_url': upload_url, 'image_key': image_key})


def handle_update(event, entity_id, table):
    body = json.loads(event.get('body', '{}'))

    # If this is an embedding confirmation (image uploaded, generate embedding now)
    if body.get('generate_embedding'):
        return _generate_and_store_embedding(entity_id, table)

    updates, values = [], {}
    if 'name' in body:
        updates.append('#n = :n')
        values[':n'] = body['name']
    if 'description' in body:
        updates.append('description = :d')
        values[':d'] = body['description']

    if not updates:
        return respond(400, {'error': 'Nothing to update'})

    kwargs = {'Key': {'entity_id': entity_id}, 'UpdateExpression': 'SET ' + ', '.join(updates),
              'ExpressionAttributeValues': values}
    if '#n' in str(updates):
        kwargs['ExpressionAttributeNames'] = {'#n': 'name'}
    table.update_item(**kwargs)
    return respond(200, {'entity_id': entity_id, 'updated': True})


def _generate_and_store_embedding(entity_id, table):
    """Generate Nova MME image embedding and store in S3 Vectors entity index."""
    entity = table.get_item(Key={'entity_id': entity_id}).get('Item')
    if not entity:
        return respond(404, {'error': 'Entity not found'})

    image_key = entity.get('image_key', '')
    project_id = entity.get('project_id', '')
    if not image_key:
        return respond(400, {'error': 'No image uploaded'})

    # Generate embedding
    from nova_embeddings import generate_image_embedding_nova
    s3_uri = f"s3://{S3_VIDEO_BUCKET}/{image_key}"
    embedding = generate_image_embedding_nova(s3_uri, purpose='GENERIC_INDEX')

    # Store in S3 Vectors entity index
    s3vectors = boto3.client('s3vectors')
    s3vectors.put_vectors(
        vectorBucketName=S3_VECTOR_BUCKET,
        indexName=f'nova-entity-{project_id}',
        vectors=[{
            'key': entity_id,
            'data': {'float32': [float(v) for v in embedding]},
            'metadata': {'entity_name': entity.get('name', '')},
        }],
    )

    table.update_item(
        Key={'entity_id': entity_id},
        UpdateExpression='SET has_embedding = :t',
        ExpressionAttributeValues={':t': True},
    )
    return respond(200, {'entity_id': entity_id, 'has_embedding': True})


def handle_delete(entity_id, table, project_id=''):
    entity = table.get_item(Key={'entity_id': entity_id}).get('Item', {})
    proj = entity.get('project_id', project_id)

    # Delete from S3 Vectors
    if entity.get('has_embedding') and proj:
        try:
            s3vectors = boto3.client('s3vectors')
            s3vectors.delete_vectors(
                vectorBucketName=S3_VECTOR_BUCKET,
                indexName=f'nova-entity-{proj}',
                keys=[entity_id],
            )
        except Exception as e:
            print(f"Warning: could not delete entity vector: {e}")

    # Delete image from S3
    if entity.get('image_key'):
        try:
            s3.delete_object(Bucket=S3_VIDEO_BUCKET, Key=entity['image_key'])
        except Exception:
            pass

    table.delete_item(Key={'entity_id': entity_id})
    return respond(200, {'entity_id': entity_id, 'deleted': True})
