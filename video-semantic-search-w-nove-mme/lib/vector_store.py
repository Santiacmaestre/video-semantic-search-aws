"""S3 Vectors storage and retrieval for Nova MME embeddings (1024d).

Per-project indices store visual, audio, and transcription vectors separately.
Entity index stores face/object embeddings for @name search.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

import boto3

s3vectors = boto3.client('s3vectors', region_name=os.getenv('AWS_REGION'))
S3_VECTOR_BUCKET = os.getenv('S3_VECTOR_BUCKET')

DIMENSION = 1024
MODALITIES = ['visual', 'audio', 'transcription']


def get_indices(project_id: str) -> Dict[str, str]:
    """Return S3 Vectors index names for a project."""
    return {m: f'nova-{m}-{project_id}' for m in MODALITIES + ['entity']}


def create_project_indices(project_id: str):
    """Create per-project S3 Vector indices (visual, audio, transcription, entity)."""
    meta = {'nonFilterableMetadataKeys': ['filename', 'segment_index', 'start_sec', 'end_sec', 'modality', 'video_id']}
    entity_meta = {'nonFilterableMetadataKeys': ['segment_indices', 'status', 'video_id', 'face_id']}

    for modality in MODALITIES + ['entity']:
        try:
            s3vectors.create_index(
                vectorBucketName=S3_VECTOR_BUCKET,
                indexName=f'nova-{modality}-{project_id}',
                dataType='float32',
                dimension=DIMENSION,
                distanceMetric='cosine',
                metadataConfiguration=entity_meta if modality == 'entity' else meta,
            )
        except Exception as e:
            if 'ConflictException' not in str(e) and 'already exists' not in str(e).lower():
                raise


def delete_project_indices(project_id: str):
    """Delete all S3 Vector indices for a project."""
    for modality in MODALITIES + ['entity']:
        try:
            s3vectors.delete_index(vectorBucketName=S3_VECTOR_BUCKET, indexName=f'nova-{modality}-{project_id}')
        except Exception:
            pass


def store_video_embeddings(video_id: str, filename: str, segments: List[Dict], project_id: str = ''):
    """Store visual and audio embeddings in S3 Vectors (parallel across modalities)."""
    indices = get_indices(project_id)
    by_modality = {m: [] for m in MODALITIES}

    for seg in segments:
        for modality in MODALITIES:
            emb = seg.get(f'{modality}_emb')
            if emb:
                by_modality[modality].append({
                    'key': f"{video_id}_seg{seg['segment_index']:04d}_{modality}",
                    'data': {'float32': [float(v) for v in emb]},
                    'metadata': {
                        'video_id': video_id,
                        'filename': filename,
                        'segment_index': str(seg['segment_index']),
                        'start_sec': str(seg['start_sec']),
                        'end_sec': str(seg['end_sec']),
                        'modality': modality,
                    },
                })

    def _store(modality):
        if by_modality[modality]:
            s3vectors.put_vectors(
                vectorBucketName=S3_VECTOR_BUCKET,
                indexName=indices[modality],
                vectors=by_modality[modality],
            )

    with ThreadPoolExecutor(max_workers=3) as pool:
        pool.map(_store, MODALITIES)
