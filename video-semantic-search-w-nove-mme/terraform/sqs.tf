# SQS queue for video processing
resource "aws_sqs_queue" "video_processing" {
  name                       = "${var.project_name}-video-processing"
  visibility_timeout_seconds = 900
  message_retention_seconds  = 1209600  # 14 days
  receive_wait_time_seconds  = 20

  tags = {
    Name = "${var.project_name}-video-processing"
  }
}

resource "aws_sqs_queue" "video_processing_dlq" {
  name = "${var.project_name}-video-processing-dlq"

  tags = {
    Name = "${var.project_name}-video-processing-dlq"
  }
}

resource "aws_sqs_queue_redrive_policy" "video_processing" {
  queue_url = aws_sqs_queue.video_processing.id

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.video_processing_dlq.arn
    maxReceiveCount     = 3
  })
}
