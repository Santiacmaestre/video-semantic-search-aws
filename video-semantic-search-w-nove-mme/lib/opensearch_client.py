"""OpenSearch managed domain client for hybrid search (S3 Vectors or nmslib engine)."""
import os
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

OPENSEARCH_ENDPOINT = os.environ.get('OPENSEARCH_ENDPOINT', '')

GENRES = ['Sports', 'News', 'Entertainment', 'Documentary', 'Education', 'Music', 'Gaming', 'Cooking', 'Travel', 'Technology', 'Business', 'Lifestyle', 'Sci-Fi', 'Mystery', 'Other']

def _index_settings(dimension=1024, vector_engine='s3_vectors'):
    if vector_engine == 'opensearch':
        method = {"engine": "nmslib", "name": "hnsw", "parameters": {"ef_construction": 256, "m": 16}}
    else:
        method = {"engine": "s3vector"}
    vec_field = {"type": "knn_vector", "dimension": dimension,
                 "space_type": "cosinesimil", "method": method}
    return {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                "video_id": {"type": "keyword"}, "segment_id": {"type": "keyword"},
                "title": {"type": "text"},
                "caption": {"type": "text", "analyzer": "english"},
                "people": {"type": "text"}, "genre": {"type": "keyword"},
                "upload_date": {"type": "date"},
                "start_sec": {"type": "float"}, "end_sec": {"type": "float"},
                "visual_vector": vec_field,
                "audio_vector": vec_field,
                "transcription_vector": vec_field,
            }
        }
    }


_cached_client = None

def _get_client():
    global _cached_client
    if _cached_client:
        return _cached_client
    if not OPENSEARCH_ENDPOINT:
        return None
    host = OPENSEARCH_ENDPOINT.replace('https://', '')
    region = os.environ.get('AWS_REGION', 'us-east-1')
    credentials = boto3.Session().get_credentials()
    auth = AWS4Auth(credentials.access_key, credentials.secret_key, region, 'es',
                    session_token=credentials.token)
    _cached_client = OpenSearch(
        hosts=[{'host': host, 'port': 443}],
        http_auth=auth, use_ssl=True, verify_certs=True,
        connection_class=RequestsHttpConnection, timeout=30
    )
    return _cached_client


def create_index(project_id: str, vector_engine: str = 's3_vectors'):
    client = _get_client()
    if not client:
        return
    index_name = f"segments-{project_id}"
    if not client.indices.exists(index=index_name):
        client.indices.create(index=index_name, body=_index_settings(vector_engine=vector_engine))
        print(f"Created OpenSearch index: {index_name}")


def delete_index(project_id: str):
    client = _get_client()
    if not client:
        return
    index_name = f"segments-{project_id}"
    try:
        client.indices.delete(index=index_name)
        print(f"Deleted OpenSearch index: {index_name}")
    except Exception as e:
        print(f"Warning: Could not delete OpenSearch index {index_name}: {e}")


def bulk_index_segments(project_id: str, documents: list, dimension: int = 1024, vector_engine: str = 's3_vectors'):
    client = _get_client()
    if not client or not documents:
        return
    index_name = f"segments-{project_id}"
    # Ensure index exists with proper schema
    if not client.indices.exists(index=index_name):
        client.indices.create(index=index_name, body=_index_settings(dimension, vector_engine))
        print(f"Created OpenSearch index: {index_name} (dim={dimension})")
    body = []
    for doc in documents:
        doc_id = f"{doc['video_id']}_{doc['segment_id']}"
        body.append({"index": {"_index": index_name, "_id": doc_id}})
        body.append(doc)
    if body:
        client.bulk(body=body)
        client.indices.refresh(index=index_name)
        print(f"Indexed {len(documents)} segments to OpenSearch")


def hybrid_search(project_id: str, query_text: str, vectors: dict, weights: list, k: int = 20, return_vectors: bool = False):
    """Execute hybrid BM25 + kNN search with inline normalization pipeline.

    Args:
        project_id: Project ID for index name
        query_text: Text query for BM25
        vectors: Dict of {modality: vector} for kNN queries
        weights: List of weights [bm25, visual, audio, transcription] summing to 1.0
        k: Number of results
    """
    client = _get_client()
    if not client:
        return []
    index_name = f"segments-{project_id}"

    queries = []
    active_weights = []

    # BM25 sub-query — skip if no text query (e.g. pure @entity visual search)
    if query_text.strip():
        queries.append({"multi_match": {
            "query": query_text,
            "fields": ["people^5", "caption", "title^5"],
            "type": "best_fields", "fuzziness": "AUTO"
        }})
        active_weights.append(weights[0])

    for i, modality in enumerate(['visual', 'audio', 'transcription']):
        if modality in vectors and weights[i + 1] >= 0.05:
            queries.append({"knn": {f"{modality}_vector": {"vector": vectors[modality], "k": k}}})
            active_weights.append(weights[i + 1])

    total = sum(active_weights)
    if total > 0:
        active_weights = [w / total for w in active_weights]

    if not queries:
        return []

    body = {
        "size": k,
        "query": {"hybrid": {"queries": queries}},
        "search_pipeline": {
            "phase_results_processors": [{
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {
                        "technique": "arithmetic_mean",
                        "parameters": {"weights": active_weights}
                    }
                }
            }]
        }
    }

    if not return_vectors:
        body["_source"] = {"excludes": ["visual_vector", "audio_vector", "transcription_vector"]}

    try:
        resp = client.search(index=index_name, body=body)
        results = []
        for hit in resp.get('hits', {}).get('hits', []):
            src = hit['_source']
            result = {
                'video_id': src.get('video_id'),
                'segment_id': src.get('segment_id'),
                'segment_index': int(src.get('segment_id', 'seg_0000').split('_')[1]) if src.get('segment_id') else 0,
                'start_sec': src.get('start_sec'),
                'end_sec': src.get('end_sec'),
                'caption': src.get('caption'),
                'people': src.get('people', []),
                'genre': src.get('genre'),
                'upload_date': src.get('upload_date', ''),
                'title': src.get('title', ''),
                'score': hit.get('_score', 0)
            }
            if return_vectors and 'visual_vector' in src:
                result['visual_vector'] = src['visual_vector']
            results.append(result)
        return results
    except Exception as e:
        print(f"OpenSearch hybrid search error: {e}")
        return []
