# Step Functions state machine for video processing pipeline (Nova MME only)

resource "aws_sfn_state_machine" "video_processing" {
  name     = "${var.project_name}-video-processing"
  role_arn = aws_iam_role.sfn_role.arn

  definition = jsonencode({
    Comment = "Nova MME video processing pipeline"
    StartAt = "ShotSegmentation"

    States = {
      ShotSegmentation = {
        Type     = "Task"
        Resource = aws_lambda_function.shot_segmentation.arn
        ResultPath = "$.shot_result"
        Retry = [{
          ErrorEquals     = ["States.ALL"]
          IntervalSeconds = 10
          MaxAttempts     = 2
          BackoffRate     = 2
        }]
        Next = "PrepareParallel"
      }

      PrepareParallel = {
        Type = "Pass"
        Parameters = {
          "video_id.$"        = "$.video_id"
          "filename.$"        = "$.filename"
          "s3_uri.$"          = "$.s3_uri"
          "project_id.$"      = "$.project_id"
          "embedding_model.$" = "$.embedding_model"
          "metadata_model.$"  = "$.metadata_model"
          "segment_duration.$" = "$.segment_duration"
          "shot_segments.$"   = "$.shot_result.segments"
        }
        Next = "ParallelProcessing"
      }

      ParallelProcessing = {
        Type = "Parallel"
        Branches = [
          # Branch 1: Nova MME Embeddings
          {
            StartAt = "Embeddings"
            States = {
              Embeddings = {
                Type     = "Task"
                Resource = aws_lambda_function.embedding.arn
                ResultPath = "$.result"
                Retry = [{
                  ErrorEquals     = ["States.ALL"]
                  IntervalSeconds = 10
                  MaxAttempts     = 2
                  BackoffRate     = 2
                }]
                End = true
              }
            }
          },
          # Branch 2: Transcription
          {
            StartAt = "Transcription"
            States = {
              Transcription = {
                Type     = "Task"
                Resource = aws_lambda_function.transcription.arn
                ResultPath = "$.result"
                Retry = [{
                  ErrorEquals     = ["States.ALL"]
                  IntervalSeconds = 10
                  MaxAttempts     = 1
                  BackoffRate     = 2
                }]
                Catch = [{
                  ErrorEquals = ["States.ALL"]
                  ResultPath  = "$.error"
                  Next        = "TranscriptionFailed"
                }]
                End = true
              }
              TranscriptionFailed = {
                Type   = "Pass"
                Result = { "transcripts" = [], "error" = "Transcription failed" }
                ResultPath = "$.result"
                End = true
              }
            }
          },
          # Branch 3: Celebrity Detection
          {
            StartAt = "CelebrityDetection"
            States = {
              CelebrityDetection = {
                Type     = "Task"
                Resource = aws_lambda_function.celebrity_detection.arn
                TimeoutSeconds = 600
                ResultPath = "$.result"
                Retry = [{
                  ErrorEquals     = ["States.ALL"]
                  IntervalSeconds = 10
                  MaxAttempts     = 1
                  BackoffRate     = 2
                }]
                Catch = [{
                  ErrorEquals = ["States.ALL"]
                  ResultPath  = "$.error"
                  Next        = "CelebrityDetectionFailed"
                }]
                End = true
              }
              CelebrityDetectionFailed = {
                Type   = "Pass"
                Result = { "celebrities" = [] }
                ResultPath = "$.result"
                End = true
              }
            }
          }
        ]
        ResultPath = "$.parallel_results"
        Next       = "PrepareCaptionInput"
      }

      PrepareCaptionInput = {
        Type = "Pass"
        Parameters = {
          "video_id.$"             = "$.video_id"
          "metadata_model.$"       = "$.metadata_model"
          "transcription_result.$" = "$.parallel_results[1].result"
        }
        ResultPath = "$.caption_input"
        Next       = "GenerateCaptions"
      }

      GenerateCaptions = {
        Type     = "Task"
        Resource = aws_lambda_function.caption.arn
        InputPath  = "$.caption_input"
        ResultPath = "$.caption_result"
        TimeoutSeconds = 600
        Retry = [{
          ErrorEquals     = ["States.ALL"]
          IntervalSeconds = 10
          MaxAttempts     = 1
          BackoffRate     = 2
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.caption_result"
          Next        = "PrepareMerge"
        }]
        Next = "PrepareMerge"
      }

      PrepareMerge = {
        Type = "Pass"
        Parameters = {
          "video_id.$"             = "$.video_id"
          "project_id.$"           = "$.project_id"
          "embedding_model.$"      = "$.embedding_model"
          "metadata_model.$"       = "$.metadata_model"
          "embedding_result.$"     = "$.parallel_results[0].result"
          "transcription_result.$" = "$.parallel_results[1].result"
          "celebrity_result.$"     = "$.parallel_results[2].result"
          "caption_result.$"       = "$.caption_result"
        }
        Next = "Merge"
      }

      Merge = {
        Type     = "Task"
        Resource = aws_lambda_function.merge.arn
        Retry = [{
          ErrorEquals     = ["States.ALL"]
          IntervalSeconds = 5
          MaxAttempts     = 2
          BackoffRate     = 2
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.merge_error"
          Next        = "MarkFailed"
        }]
        End = true
      }

      MarkFailed = {
        Type     = "Task"
        Resource = aws_lambda_function.merge.arn
        Parameters = {
          "video_id.$"        = "$.video_id"
          "project_id.$"      = "$.project_id"
          "embedding_model.$" = "$.embedding_model"
          "mark_failed"       = true
        }
        End = true
      }
    }
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  depends_on = [aws_iam_role_policy.sfn_policy, aws_cloudwatch_log_group.sfn]
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/states/${var.project_name}-video-processing"
  retention_in_days = 14
}

resource "aws_iam_role" "sfn_role" {
  name = "${var.project_name}-sfn-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sfn_policy" {
  name = "${var.project_name}-sfn-policy"
  role = aws_iam_role.sfn_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = "arn:aws:lambda:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups", "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      }
    ]
  })
}
