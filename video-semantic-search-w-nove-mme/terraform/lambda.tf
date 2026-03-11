# Lambda Layer
resource "aws_lambda_layer_version" "shared" {
  filename            = "${path.module}/../lambda/layer.zip"
  layer_name          = "${var.project_name}-shared-layer"
  compatible_runtimes = [var.lambda_runtime]
  source_code_hash    = fileexists("${path.module}/../lambda/layer.zip") ? filebase64sha256("${path.module}/../lambda/layer.zip") : null

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

# Search Lambda Function
resource "aws_lambda_function" "search" {
  filename         = "${path.module}/../lambda/search_function.zip"
  function_name    = "${var.project_name}-search-function"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "search_function.lambda_handler"
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  source_code_hash = fileexists("${path.module}/../lambda/search_function.zip") ? filebase64sha256("${path.module}/../lambda/search_function.zip") : null

  layers = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = {
      S3_VECTOR_BUCKET       = local.vector_bucket_name
      VIDEOS_TABLE           = aws_dynamodb_table.videos.name
      SEGMENTS_TABLE         = aws_dynamodb_table.segments.name
      ENTITIES_TABLE         = aws_dynamodb_table.entities.name
      CLAUDE_MODEL_ID        = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
      NOVA_ANALYZER_MODEL_ID = "arn:aws:bedrock:us-east-1:376678947624:custom-model-deployment/4ezx3jono3xp"
      PROJECTS_TABLE         = aws_dynamodb_table.projects.name
      OPENSEARCH_ENDPOINT    = "https://${aws_opensearch_domain.segments.endpoint}"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.search_function
  ]

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

# Upload Lambda Function
resource "aws_lambda_function" "upload" {
  filename         = "${path.module}/../lambda/upload_function.zip"
  function_name    = "${var.project_name}-upload-function"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "upload_function.lambda_handler"
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  source_code_hash = fileexists("${path.module}/../lambda/upload_function.zip") ? filebase64sha256("${path.module}/../lambda/upload_function.zip") : null

  environment {
    variables = {
      S3_VIDEO_BUCKET = aws_s3_bucket.videos.id
      VIDEOS_TABLE    = aws_dynamodb_table.videos.name
      SQS_QUEUE_URL   = aws_sqs_queue.video_processing.url
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.upload_function
  ]

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

# Video Lambda Function
resource "aws_lambda_function" "video" {
  filename         = "${path.module}/../lambda/video_function.zip"
  function_name    = "${var.project_name}-video-function"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "video_function.lambda_handler"
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  source_code_hash = fileexists("${path.module}/../lambda/video_function.zip") ? filebase64sha256("${path.module}/../lambda/video_function.zip") : null

  environment {
    variables = {
      VIDEOS_TABLE = aws_dynamodb_table.videos.name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.video_function
  ]

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

# Entity Lambda Function
resource "aws_lambda_function" "entity" {
  filename         = "${path.module}/../lambda/entity_function.zip"
  function_name    = "${var.project_name}-entity-function"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "entity_function.lambda_handler"
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  source_code_hash = fileexists("${path.module}/../lambda/entity_function.zip") ? filebase64sha256("${path.module}/../lambda/entity_function.zip") : null

  environment {
    variables = {
      ENTITIES_TABLE   = aws_dynamodb_table.entities.name
      PROJECTS_TABLE   = aws_dynamodb_table.projects.name
      S3_VIDEO_BUCKET  = aws_s3_bucket.videos.id
      S3_VECTOR_BUCKET = local.vector_bucket_name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.entity_function
  ]

  lifecycle {
    ignore_changes = [source_code_hash, layers]
  }
}

# Lambda permissions for API Gateway
resource "aws_lambda_permission" "search" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.search.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "upload" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.upload.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "video" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.video.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "entity" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.entity.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}


# ECR Repository for Docker-based Lambda functions
# Used by: shot-segmentation, celebrity-detection, caption

resource "aws_ecr_repository" "worker" {
  name                 = "${var.project_name}-worker"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Push Docker image to ECR
resource "null_resource" "push_worker_image" {
  triggers = {
    ecr_repo = aws_ecr_repository.worker.repository_url
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws ecr get-login-password --region ${var.aws_region} --profile ${var.aws_profile} | \
        docker login --username AWS --password-stdin ${aws_ecr_repository.worker.repository_url}
      docker tag video-search-worker:latest ${aws_ecr_repository.worker.repository_url}:latest
      docker push ${aws_ecr_repository.worker.repository_url}:latest
    EOT
  }

  depends_on = [aws_ecr_repository.worker]
}

# Old worker S3 trigger - replaced by orchestrator
# resource "aws_lambda_permission" "s3_invoke_worker" {
#   statement_id  = "AllowS3Invoke"
#   action        = "lambda:InvokeFunction"
#   function_name = aws_lambda_function.worker.function_name
#   principal     = "s3.amazonaws.com"
#   source_arn    = aws_s3_bucket.videos.arn
# }
# 
# resource "aws_s3_bucket_notification" "video_upload" {
#   bucket = aws_s3_bucket.videos.id
# 
#   lambda_function {
#     lambda_function_arn = aws_lambda_function.worker.arn
#     events              = ["s3:ObjectCreated:*"]
#     filter_prefix       = "uploads/"
#   }
# 
#   depends_on = [aws_lambda_permission.s3_invoke_worker]
# }


# Project Lambda Function
resource "aws_lambda_function" "project" {
  filename         = "${path.module}/../lambda/project_function.zip"
  function_name    = "${var.project_name}-project-function"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "project_function.lambda_handler"
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  kms_key_arn      = ""
  source_code_hash = fileexists("${path.module}/../lambda/project_function.zip") ? filebase64sha256("${path.module}/../lambda/project_function.zip") : null

  environment {
    variables = {
      PROJECTS_TABLE      = aws_dynamodb_table.projects.name
      VIDEOS_TABLE        = aws_dynamodb_table.videos.name
      SEGMENTS_TABLE      = aws_dynamodb_table.segments.name
      ENTITIES_TABLE      = aws_dynamodb_table.entities.name
      S3_VECTOR_BUCKET    = local.vector_bucket_name
      S3_VIDEO_BUCKET     = aws_s3_bucket.videos.id
      OPENSEARCH_ENDPOINT = "https://${aws_opensearch_domain.segments.endpoint}"
    }
  }

  layers = [aws_lambda_layer_version.shared.arn]

  lifecycle {
    ignore_changes = [source_code_hash, layers]
  }
}

resource "aws_lambda_permission" "project" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.project.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# ========== Step Functions Pipeline Lambdas ==========

# Orchestrator - lightweight, triggered by S3
resource "aws_lambda_function" "orchestrator" {
  function_name = "${var.project_name}-orchestrator"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "orchestrator_function.lambda_handler"
  runtime       = var.lambda_runtime
  timeout       = 30
  memory_size   = 256
  filename      = "${path.module}/../lambda/orchestrator_function.zip"

  environment {
    variables = {
      VIDEOS_TABLE      = aws_dynamodb_table.videos.name
      PROJECTS_TABLE    = aws_dynamodb_table.projects.name
      STATE_MACHINE_ARN = aws_sfn_state_machine.video_processing.arn
      S3_VIDEO_BUCKET   = aws_s3_bucket.videos.id
      AWS_ACCOUNT_ID    = data.aws_caller_identity.current.account_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.orchestrator]
  lifecycle { ignore_changes = [filename, source_code_hash] }
}

resource "aws_cloudwatch_log_group" "orchestrator" {
  name              = "/aws/lambda/${var.project_name}-orchestrator"
  retention_in_days = 14
}

# S3 trigger for orchestrator (replaces worker trigger)
resource "aws_lambda_permission" "s3_orchestrator" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.orchestrator.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.videos.arn
}

resource "aws_s3_bucket_notification" "video_upload" {
  bucket = aws_s3_bucket.videos.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.orchestrator.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
  }

  depends_on = [aws_lambda_permission.s3_orchestrator]
}

# Shot Segmentation - Docker (needs ffmpeg)
resource "aws_lambda_function" "shot_segmentation" {
  function_name = "${var.project_name}-shot-segmentation"
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.worker.repository_url}:latest"
  timeout       = 300
  memory_size   = 1024

  image_config {
    command = ["shot_segmentation_function.lambda_handler"]
  }

  ephemeral_storage { size = 10240 }

  environment {
    variables = {
      S3_VIDEO_BUCKET = aws_s3_bucket.videos.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.shot_segmentation]
  lifecycle { ignore_changes = [image_uri] }
}

resource "aws_cloudwatch_log_group" "shot_segmentation" {
  name              = "/aws/lambda/${var.project_name}-shot-segmentation"
  retention_in_days = 14
}

# Embedding Lambda
resource "aws_lambda_function" "embedding" {
  function_name = "${var.project_name}-embedding"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "embedding_function.lambda_handler"
  runtime       = var.lambda_runtime
  timeout       = 900
  memory_size   = 512
  filename      = "${path.module}/../lambda/embedding_function.zip"
  layers        = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = {
      S3_VIDEO_BUCKET  = aws_s3_bucket.videos.id
      S3_VECTOR_BUCKET = local.vector_bucket_name
      NOVA_MODEL_ID    = "amazon.nova-2-multimodal-embeddings-v1:0"
      AWS_ACCOUNT_ID   = data.aws_caller_identity.current.account_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.embedding]
  lifecycle { ignore_changes = [filename, source_code_hash, layers] }
}

resource "aws_cloudwatch_log_group" "embedding" {
  name              = "/aws/lambda/${var.project_name}-embedding"
  retention_in_days = 14
}

# Transcription Lambda
resource "aws_lambda_function" "transcription" {
  function_name = "${var.project_name}-transcription"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "transcription_function.lambda_handler"
  runtime       = var.lambda_runtime
  timeout       = 900
  memory_size   = 512
  filename      = "${path.module}/../lambda/transcription_function.zip"
  layers        = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = {
      S3_VIDEO_BUCKET  = aws_s3_bucket.videos.id
      S3_VECTOR_BUCKET = local.vector_bucket_name
      NOVA_MODEL_ID    = "amazon.nova-2-multimodal-embeddings-v1:0"
    }
  }

  depends_on = [aws_cloudwatch_log_group.transcription]
  lifecycle { ignore_changes = [filename, source_code_hash, layers] }
}

resource "aws_cloudwatch_log_group" "transcription" {
  name              = "/aws/lambda/${var.project_name}-transcription"
  retention_in_days = 14
}

# Celebrity Detection Lambda
resource "aws_lambda_function" "celebrity_detection" {
  function_name = "${var.project_name}-celebrity-detection"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "celebrity_detection_function.lambda_handler"
  runtime       = var.lambda_runtime
  timeout       = 600
  memory_size   = 256
  filename      = "${path.module}/../lambda/celebrity_detection_function.zip"

  environment {
    variables = {
      S3_VIDEO_BUCKET = aws_s3_bucket.videos.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.celebrity_detection]
  lifecycle { ignore_changes = [filename, source_code_hash] }
}

resource "aws_cloudwatch_log_group" "celebrity_detection" {
  name              = "/aws/lambda/${var.project_name}-celebrity-detection"
  retention_in_days = 14
}

# Caption Generation Lambda
resource "aws_lambda_function" "caption" {
  function_name = "${var.project_name}-caption"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "caption_function.handler"
  runtime       = var.lambda_runtime
  timeout       = 600
  memory_size   = 512
  filename      = "${path.module}/../lambda/caption_function.zip"

  environment {
    variables = {
      S3_VIDEO_BUCKET = aws_s3_bucket.videos.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.caption]
  lifecycle { ignore_changes = [filename, source_code_hash] }
}

resource "aws_cloudwatch_log_group" "caption" {
  name              = "/aws/lambda/${var.project_name}-caption"
  retention_in_days = 14
}

# Merge Lambda
resource "aws_lambda_function" "merge" {
  function_name = "${var.project_name}-merge"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "merge_function.lambda_handler"
  runtime       = var.lambda_runtime
  timeout       = 120
  memory_size   = 512
  filename      = "${path.module}/../lambda/merge_function.zip"
  layers        = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = {
      VIDEOS_TABLE        = aws_dynamodb_table.videos.name
      SEGMENTS_TABLE      = aws_dynamodb_table.segments.name
      PROJECTS_TABLE      = aws_dynamodb_table.projects.name
      S3_VECTOR_BUCKET    = local.vector_bucket_name
      S3_VIDEO_BUCKET     = aws_s3_bucket.videos.id
      OPENSEARCH_ENDPOINT = "https://${aws_opensearch_domain.segments.endpoint}"
    }
  }

  depends_on = [aws_cloudwatch_log_group.merge]
  lifecycle { ignore_changes = [filename, source_code_hash, layers] }
}

resource "aws_cloudwatch_log_group" "merge" {
  name              = "/aws/lambda/${var.project_name}-merge"
  retention_in_days = 14
}
