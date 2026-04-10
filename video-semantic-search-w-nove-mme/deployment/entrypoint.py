"""Fargate entrypoint for shot segmentation.

Reads task input from TASK_INPUT env var (JSON), runs segmentation,
writes result JSON to S3 at processing/{video_id}/segmentation_result.json.
"""
import json
import os
import sys
import boto3

# shot_segmentation_function.py is in the same directory in the Docker image
from shot_segmentation_function import run_segmentation

s3_client = boto3.client('s3')


def main():
    task_input_raw = os.environ.get('TASK_INPUT')
    if not task_input_raw:
        print("ERROR: TASK_INPUT environment variable not set")
        sys.exit(1)

    try:
        task_input = json.loads(task_input_raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse TASK_INPUT: {e}")
        sys.exit(1)

    video_id = task_input['video_id']
    s3_uri = task_input['s3_uri']
    target_duration = task_input.get('segment_duration', 10)
    pre_segments = task_input.get('segments')
    bucket = os.environ.get('S3_VIDEO_BUCKET', '')

    print(f"Starting segmentation: video_id={video_id}, s3_uri={s3_uri}")

    result = run_segmentation(
        video_id=video_id,
        s3_uri=s3_uri,
        target_duration=target_duration,
        pre_segments=pre_segments,
    )

    # Write result to S3 for the bridge Lambda to read
    result_key = f"processing/{video_id}/segmentation_result.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=result_key,
        Body=json.dumps(result),
        ContentType='application/json',
    )
    print(f"Result written to s3://{bucket}/{result_key}")
    print(f"Segments: {len(result['segments'])}, Duration: {result['video_duration']}s")


if __name__ == '__main__':
    main()
