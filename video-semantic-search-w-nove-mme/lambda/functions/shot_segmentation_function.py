"""Shot segmentation Lambda — ffmpeg scene detection with smart segment boundaries."""
import os
import subprocess
import tempfile
import boto3

s3_client = boto3.client('s3')
S3_VIDEO_BUCKET = os.environ.get('S3_VIDEO_BUCKET', '')

SCENE_THRESHOLD = 0.3
MIN_SEGMENT_SEC = 4


def lambda_handler(event, context):
    video_id = event['video_id']
    s3_uri = event['s3_uri']
    target_duration = event.get('segment_duration', 10)
    max_duration = int(target_duration * 1.5)
    pre_segments = event.get('segments')  # Pre-defined segments (skip scene detection)

    bucket, key = s3_uri.replace('s3://', '').split('/', 1)

    # Download video
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        s3_client.download_fileobj(bucket, key, tmp)
        video_path = tmp.name

    try:
        duration = _get_duration(video_path)
        print(f"Video duration: {duration}s")

        if pre_segments:
            # Extract-only mode: segments provided, just extract clips
            segments = pre_segments
            print(f"Extract-only: {len(segments)} pre-defined segments")
        else:
            # Full mode (Nova MME): scene detection + segment building
            scene_changes = _detect_scenes(video_path)
            print(f"Detected {len(scene_changes)} scene changes")
            segments = _build_smart_segments(scene_changes, duration, target_duration, MIN_SEGMENT_SEC, max_duration)
            print(f"Built {len(segments)} smart segments")

        # Extract clips and upload to S3
        for seg in segments:
            clip_key = f"clips/{video_id}/seg_{seg['segment_index']:04d}.mp4"
            clip_path = f"/tmp/seg_{seg['segment_index']}.mp4"

            subprocess.run([
                'ffmpeg', '-y', '-ss', str(seg['start_sec']), '-to', str(seg['end_sec']),
                '-i', video_path, '-c', 'copy', '-avoid_negative_ts', '1', clip_path
            ], capture_output=True)

            s3_client.upload_file(clip_path, S3_VIDEO_BUCKET, clip_key)
            seg['clip_s3_uri'] = f"s3://{S3_VIDEO_BUCKET}/{clip_key}"
            os.unlink(clip_path)

        return {'segments': segments, 'video_duration': duration}

    finally:
        os.unlink(video_path)


def _get_duration(video_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', video_path],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def _detect_scenes(video_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'frame=pts_time', '-of', 'csv=p=0',
         '-f', 'lavfi', f"movie={video_path},select='gt(scene\\,{SCENE_THRESHOLD})'"],
        capture_output=True, text=True
    )
    timestamps = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        try:
            timestamps.append(float(line))
        except ValueError:
            continue
    return sorted(timestamps)


def _build_smart_segments(scene_changes, video_duration, target_duration=10, min_dur=4, max_dur=15):
    segments = []
    current_start = 0.0

    while current_start < video_duration - 1.0:
        ideal_end = current_start + target_duration

        # Find scene changes in acceptable window
        candidates = [t for t in scene_changes
                      if current_start + min_dur <= t <= current_start + max_dur]

        if candidates:
            seg_end = min(candidates, key=lambda t: abs(t - ideal_end))
        else:
            seg_end = current_start + target_duration

        seg_end = min(seg_end, video_duration)

        segments.append({
            'segment_index': len(segments),
            'start_sec': round(current_start, 2),
            'end_sec': round(seg_end, 2)
        })
        current_start = seg_end

    return segments
