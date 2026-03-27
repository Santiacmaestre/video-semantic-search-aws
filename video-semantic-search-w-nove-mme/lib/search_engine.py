"""Hybrid search engine — OpenSearch BM25 + kNN with LLM-weighted fusion.

Pipeline:
  1. Parallel: LLM weight analysis + Nova MME embedding generation
  2. Build hybrid query (BM25 + per-modality kNN)
  3. OpenSearch executes with inline min-max normalization + weighted arithmetic mean
"""
import concurrent.futures
import os
import re
import time
from typing import Dict

import boto3
from nova_embeddings import generate_text_embedding_nova
from prompt_analyzer import analyze_query_weights


def search_with_fusion(query_text: str, top_k: int = None, project_id: str = '',
                       analyzer_model_id: str = None, **kwargs) -> Dict:
    """Run a hybrid search query and return ranked results with scores.

    Args:
        query_text: Natural language query, optionally with @entity tokens.
        top_k: Max results to return (default 20).
        project_id: Scope search to a specific project index.
        analyzer_model_id: Override the LLM used for weight analysis.

    Returns:
        Dict with keys: results, weights, reasoning, total, timings.
    """
    timings = {}

    # Look up project's vector engine
    vector_engine = 's3_vectors'
    if project_id:
        try:
            proj_table = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION', 'us-east-1')).Table(os.environ.get('PROJECTS_TABLE', 'video-search-v2-projects'))
            vector_engine = proj_table.get_item(Key={'project_id': project_id}).get('Item', {}).get('vector_engine', 's3_vectors')
        except Exception:
            pass

    # Extract @entity names before cleaning
    entity_names = re.findall(r'@(\w+)', query_text)
    clean_query = re.sub(r'@\w+', '', query_text).strip()
    # Remove stray # tokens
    clean_query = re.sub(r'#', '', clean_query).strip()
    pure_entity_search = bool(entity_names) and not clean_query

    if not entity_names and not clean_query:
        return {'results': [], 'weights': {}, 'reasoning': 'Empty query', 'total': 0, 'timings': {}}

    # --- Phase 1: Parallel preprocessing -------------------------------------------
    t0 = time.time()
    vectors = {}
    entity_emb = None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        # Entity embedding lookup (parallel with everything else)
        entity_fut = None
        if entity_names and project_id:
            entity_fut = pool.submit(_get_entity_embedding, entity_names[0], project_id)

        if pure_entity_search:
            weights_data = {'visual': 1.0, 'audio': 0.0, 'transcription': 0.0, 'metadata': 0.0,
                            'reasoning': f'Pure entity visual search for @{entity_names[0]}'}
        else:
            analyze_fut = pool.submit(analyze_query_weights, clean_query, analyzer_model_id)
            embed_futs = [
                pool.submit(_safe_embed, clean_query, 'visual', 'GENERIC_RETRIEVAL'),
                pool.submit(_safe_embed, clean_query, 'audio', 'GENERIC_RETRIEVAL'),
                pool.submit(_safe_embed, clean_query, 'transcription', 'TEXT_RETRIEVAL'),
            ]
            weights_data = analyze_fut.result()
            for fut in embed_futs:
                modality, vec = fut.result()
                if vec:
                    vectors[modality] = vec

        if entity_fut:
            entity_emb = entity_fut.result()

        # Pure entity search: use entity embedding as visual vector
        if pure_entity_search and entity_emb:
            vectors['visual'] = entity_emb

    timings['preprocessing_ms'] = int((time.time() - t0) * 1000)

    # --- Phase 2: OpenSearch hybrid search -----------------------------------------
    t0 = time.time()
    from opensearch_client import hybrid_search

    vectors = {m: v for m, v in vectors.items() if weights_data.get(m, 0) >= 0.05}
    weights_list = [
        weights_data.get('metadata', 0.2),
        weights_data.get('visual', 0.3),
        weights_data.get('audio', 0.2),
        weights_data.get('transcription', 0.3),
    ]

    k = top_k or 20
    need_vectors = bool(entity_emb and not pure_entity_search and vector_engine == 'opensearch')
    hits = hybrid_search(project_id, clean_query, vectors, weights_list, k, return_vectors=need_vectors)
    timings['search_ms'] = int((time.time() - t0) * 1000)

    # --- Phase 3: Format results ---------------------------------------------------
    results = [
        {
            'video_id': h.get('video_id', ''),
            'filename': None,
            'segment_index': h.get('segment_index', 0),
            'start_sec': h.get('start_sec', 0),
            'end_sec': h.get('end_sec', 0),
            'caption': h.get('caption', ''),
            'people': h.get('people', []),
            'genre': h.get('genre', ''),
            'upload_date': h.get('upload_date', ''),
            'title': h.get('title', ''),
            'combined_score': h.get('score', 0),
        }
        for h in hits
    ]

    if results:
        _enrich_filenames(results)

    # --- Phase 4: Entity re-rank (post-sort) --------------------------------------
    # If @entity with embedding in a combined search, fetch visual vectors for each
    # result and re-rank by blending search score with entity cosine similarity.
    if entity_emb and not pure_entity_search and results:
        t0 = time.time()
        _entity_rerank(results, entity_emb, project_id, vector_engine=vector_engine)
        timings['entity_rerank_ms'] = int((time.time() - t0) * 1000)

    return {
        'results': results,
        'weights': {k: v for k, v in weights_data.items() if k != 'reasoning'},
        'reasoning': weights_data.get('reasoning', ''),
        'total': len(results),
        'timings': timings,
    }


def _safe_embed(text, modality, purpose):
    """Generate a text embedding, returning (modality, vector) or (modality, None) on error."""
    try:
        return modality, generate_text_embedding_nova(text, purpose=purpose)
    except Exception as e:
        print(f"Embedding error ({modality}): {e}")
        return modality, None


def _entity_rerank(results, entity_emb, project_id, blend=0.5, vector_engine='s3_vectors'):
    """Re-rank results by blending search score with entity visual similarity.

    Fetches visual vectors from S3 Vectors (or OpenSearch _source) for each result,
    computes cosine similarity against the entity embedding, and produces a blended score:
        final = (1 - blend) * search_score + blend * entity_similarity
    """
    if vector_engine == 'opensearch':
        # Vectors already in results from OpenSearch _source
        vec_map = {f"{r['video_id']}_{r['segment_index']}": r.pop('visual_vector', None) for r in results}
    else:
        s3v = boto3.client('s3vectors', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        bucket = os.environ.get('S3_VECTOR_BUCKET', '')
        keys = [f"{r['video_id']}_seg{r['segment_index']:04d}_visual" for r in results]
        try:
            resp = s3v.get_vectors(vectorBucketName=bucket, indexName=f'nova-visual-{project_id}', keys=keys, returnData=True)
            vec_map = {v['key']: v['data']['float32'] for v in resp.get('vectors', [])}
        except Exception as e:
            print(f"Entity rerank vector fetch failed: {e}")
            return

    for r in results:
        key = f"{r['video_id']}_{r['segment_index']}" if vector_engine == 'opensearch' else f"{r['video_id']}_seg{r['segment_index']:04d}_visual"
        vis_vec = vec_map.get(key)
        if vis_vec:
            sim = _cosine_sim(entity_emb, vis_vec)
            r['entity_similarity'] = round(sim, 4)
            r['combined_score'] = (1 - blend) * r['combined_score'] + blend * sim
        else:
            r['entity_similarity'] = 0.0

    results.sort(key=lambda r: r['combined_score'], reverse=True)


def _cosine_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _enrich_filenames(results):
    """Look up filenames from DynamoDB for each unique video_id in results."""
    dynamodb = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION', 'us-east-1'))
    table = dynamodb.Table(os.environ.get('VIDEOS_TABLE', 'video-search-v2-videos'))
    cache = {}
    for r in results:
        vid = r['video_id']
        if vid not in cache:
            try:
                cache[vid] = table.get_item(Key={'video_id': vid}).get('Item', {}).get('filename', '')
            except Exception:
                cache[vid] = ''
        r['filename'] = cache[vid]


def _get_entity_embedding(entity_name, project_id):
    """Look up an entity's image embedding from S3 Vectors by name."""
    try:
        dynamodb = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        table = dynamodb.Table(os.environ.get('ENTITIES_TABLE', 'video-search-v2-entities'))
        # Scan for entity by name (case-insensitive match)
        resp = table.scan(
            FilterExpression='project_id = :pid',
            ExpressionAttributeValues={':pid': project_id},
        )
        entity = None
        for item in resp.get('Items', []):
            if item.get('name', '').lower().replace(' ', '_') == entity_name.lower():
                entity = item
                break
        if not entity or not entity.get('has_embedding'):
            return None

        s3v = boto3.client('s3vectors', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        result = s3v.get_vectors(
            vectorBucketName=os.environ.get('S3_VECTOR_BUCKET', ''),
            indexName=f'nova-entity-{project_id}',
            keys=[entity['entity_id']],
            returnData=True,
        )
        vectors = result.get('vectors', [])
        return vectors[0]['data']['float32'] if vectors else None
    except Exception as e:
        print(f"Entity embedding lookup failed: {e}")
        return None
