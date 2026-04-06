"""Transcription Lambda — AWS Transcribe with sentence-aware segment alignment."""
import json
import os
import sys
import uuid
import time
sys.path.insert(0, '/opt/python')

import boto3

aws_region = os.environ.get('AWS_REGION', 'us-east-1')
transcribe_client = boto3.client('transcribe', region_name=aws_region)
s3_client = boto3.client('s3', region_name=aws_region)
bedrock_runtime = boto3.client('bedrock-runtime', region_name=aws_region)

S3_VIDEO_BUCKET = os.environ.get('S3_VIDEO_BUCKET', '')
S3_VECTOR_BUCKET = os.environ.get('S3_VECTOR_BUCKET', '')
NOVA_MODEL_ID = os.environ.get('NOVA_MODEL_ID', 'amazon.nova-2-multimodal-embeddings-v1:0')
NOVA_DIMENSION = 1024


def lambda_handler(event, context):
    if event.get('embedding_model') != 'nova-mme':
        return {'transcripts': [], 'skipped': True}

    video_id = event['video_id']
    s3_uri = event['s3_uri']
    project_id = event.get('project_id', '')

    # Load segments from S3 (avoids Step Functions payload limit)
    segments_s3_key = event.get('segments_s3_key', '')
    if segments_s3_key:
        obj = s3_client.get_object(Bucket=S3_VIDEO_BUCKET, Key=segments_s3_key)
        shot_segments = json.loads(obj['Body'].read()).get('segments', [])
    else:
        shot_segments = event.get('shot_segments', [])

    # Run Transcribe
    words = _transcribe(s3_uri)
    if not words:
        return {'transcripts': [], 'error': 'No words transcribed'}

    # Align to segments with sentence awareness
    transcripts = _align_to_segments(words, shot_segments)

    # Generate Nova text embeddings and store
    from vector_store import get_indices
    indices = get_indices(project_id)
    s3v = boto3.client('s3vectors', region_name=aws_region)
    vector_bucket = os.environ.get('S3_VECTOR_BUCKET', '')

    for t in transcripts:
        if not t['text'].strip():
            continue
        try:
            emb = _nova_text_embedding(t['text'])
            s3v.put_vectors(
                vectorBucketName=vector_bucket,
                indexName=indices['transcription'],
                vectors=[{
                    'key': f"{video_id}_seg{t['segment_index']:04d}_transcription",
                    'data': {'float32': [float(v) for v in emb]},
                    'metadata': {
                        'video_id': video_id,
                        'filename': event.get('filename', ''),
                        'segment_index': str(t['segment_index']),
                        'start_sec': str(t['start_sec']),
                        'end_sec': str(t['end_sec']),
                        'modality': 'transcription'
                    }
                }]
            )
        except Exception as e:
            print(f"Error storing transcript embedding seg {t['segment_index']}: {e}")

    print(f"Transcribed {len(transcripts)} segments from {len(words)} words")

    # Write transcripts to S3 (avoids Step Functions payload limit)
    transcripts_data = [{'segment_index': t['segment_index'], 'text': t['text']} for t in transcripts]
    transcripts_key = f"metadata/{video_id}/transcripts.json"
    s3_client.put_object(Bucket=S3_VIDEO_BUCKET, Key=transcripts_key, Body=json.dumps(transcripts_data), ContentType='application/json')

    return {'transcripts_s3_key': transcripts_key, 'transcript_count': len(transcripts)}


def _transcribe(s3_uri):
    bucket = s3_uri.replace('s3://', '').split('/')[0]
    job_name = f"sfn-transcribe-{uuid.uuid4().hex[:12]}"

    transcribe_client.start_transcription_job(
        TranscriptionJobName=job_name,
        Media={'MediaFileUri': s3_uri},
        MediaFormat='mp4',
        LanguageCode='en-US',
        OutputBucketName=bucket,
        OutputKey=f'transcripts/{job_name}.json'
    )

    while True:
        resp = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
        status = resp['TranscriptionJob']['TranscriptionJobStatus']
        if status == 'COMPLETED':
            break
        elif status == 'FAILED':
            raise Exception(f"Transcribe failed: {resp['TranscriptionJob'].get('FailureReason', '')}")
        time.sleep(3)

    obj = s3_client.get_object(Bucket=bucket, Key=f'transcripts/{job_name}.json')
    data = json.loads(obj['Body'].read().decode())

    words = []
    for item in data.get('results', {}).get('items', []):
        if item['type'] == 'pronunciation':
            words.append({
                'word': item['alternatives'][0]['content'],
                'start': float(item['start_time']),
                'end': float(item['end_time'])
            })
        elif item['type'] == 'punctuation' and words:
            words[-1]['word'] += item['alternatives'][0]['content']

    # Cleanup
    try:
        transcribe_client.delete_transcription_job(TranscriptionJobName=job_name)
        s3_client.delete_object(Bucket=bucket, Key=f'transcripts/{job_name}.json')
    except:
        pass

    return words


def _align_to_segments(words, segments, overlap=2.0, max_extend_words=20):
    """Align transcript to segments with sentence-aware boundary extension."""
    if not words or not segments:
        return []

    result = []
    for seg in segments:
        start = seg['start_sec']
        end = seg['end_sec']

        # Find words overlapping this segment
        seg_word_indices = [i for i, w in enumerate(words)
                           if w['end'] >= start - overlap and w['start'] <= end + overlap]

        if not seg_word_indices:
            result.append({'segment_index': seg['segment_index'], 'start_sec': start, 'end_sec': end, 'text': ''})
            continue

        first_idx = seg_word_indices[0]
        last_idx = seg_word_indices[-1]

        # Extend backward to sentence start
        for i in range(first_idx - 1, max(first_idx - max_extend_words, -1), -1):
            if i < 0:
                first_idx = 0
                break
            if words[i]['word'].endswith(('.', '?', '!')):
                first_idx = i + 1
                break

        # Extend forward to sentence end
        for i in range(last_idx + 1, min(last_idx + max_extend_words, len(words))):
            last_idx = i
            if words[i]['word'].endswith(('.', '?', '!')):
                break

        text = ' '.join(words[i]['word'] for i in range(first_idx, last_idx + 1))
        result.append({'segment_index': seg['segment_index'], 'start_sec': start, 'end_sec': end, 'text': text})

    return result


def _nova_text_embedding(text):
    request_body = {
        'taskType': 'SINGLE_EMBEDDING',
        'singleEmbeddingParams': {
            'embeddingPurpose': 'GENERIC_INDEX',
            'embeddingDimension': NOVA_DIMENSION,
            'text': {'truncationMode': 'END', 'value': text}
        }
    }
    response = bedrock_runtime.invoke_model(
        body=json.dumps(request_body), modelId=NOVA_MODEL_ID,
        accept='application/json', contentType='application/json'
    )
    return json.loads(response['body'].read())['embeddings'][0]['embedding']
