"""Celebrity Detection Lambda - Rekognition Video async API (M2C pattern).
Starts StartCelebrityRecognition, polls GetCelebrityRecognition, returns results.
"""
import json
import os
import time
import boto3

rekognition = boto3.client('rekognition', region_name=os.environ.get('AWS_REGION', 'us-east-1'))


def lambda_handler(event, context):
    video_id = event['video_id']
    s3_uri = event['s3_uri']
    bucket, key = s3_uri.replace('s3://', '').split('/', 1)

    # Start async celebrity recognition
    try:
        start_resp = rekognition.start_celebrity_recognition(
            Video={'S3Object': {'Bucket': bucket, 'Name': key}},
            ClientRequestToken=video_id[:64]
        )
        job_id = start_resp['JobId']
        print(f"Started celebrity recognition: {job_id}")
    except Exception as e:
        print(f"Failed to start celebrity recognition: {e}")
        return {'celebrities': [], 'error': str(e)}

    # Poll until complete
    while True:
        resp = rekognition.get_celebrity_recognition(JobId=job_id, SortBy='TIMESTAMP')
        status = resp['JobStatus']
        if status == 'SUCCEEDED':
            break
        elif status == 'FAILED':
            print(f"Celebrity recognition failed: {resp.get('StatusMessage')}")
            return {'celebrities': [], 'error': resp.get('StatusMessage', 'Failed')}
        time.sleep(5)

    # Collect all pages
    celebrities = {}
    next_token = None
    while True:
        kwargs = {'JobId': job_id, 'SortBy': 'TIMESTAMP'}
        if next_token:
            kwargs['NextToken'] = next_token
        resp = rekognition.get_celebrity_recognition(**kwargs)

        for celeb_item in resp.get('Celebrities', []):
            celeb = celeb_item.get('Celebrity', {})
            name = celeb.get('Name', '')
            celeb_id = celeb.get('Id', '')
            if not name:
                continue
            ts_ms = celeb_item.get('Timestamp', 0)
            ts_sec = ts_ms / 1000.0
            confidence = celeb.get('Confidence', 0)
            bbox = celeb.get('BoundingBox', {})

            if celeb_id not in celebrities:
                celebrities[celeb_id] = {
                    'celebrity_id': celeb_id,
                    'name': name,
                    'timestamps': [],
                    'best_confidence': 0,
                    'best_bbox': {}
                }
            celebrities[celeb_id]['timestamps'].append(ts_sec)
            if confidence > celebrities[celeb_id]['best_confidence']:
                celebrities[celeb_id]['best_confidence'] = confidence
                celebrities[celeb_id]['best_bbox'] = bbox

        next_token = resp.get('NextToken')
        if not next_token:
            break

    result = list(celebrities.values())
    print(f"Found {len(result)} celebrities with {sum(len(c['timestamps']) for c in result)} total detections")
    return {'celebrities': result}
