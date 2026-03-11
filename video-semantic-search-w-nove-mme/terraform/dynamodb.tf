# DynamoDB table for videos
resource "aws_dynamodb_table" "videos" {
  name           = "${var.project_name}-videos"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "video_id"

  attribute {
    name = "video_id"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  tags = { Name = "${var.project_name}-videos" }
}

# DynamoDB table for segments
resource "aws_dynamodb_table" "segments" {
  name           = "${var.project_name}-segments"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "video_id"
  range_key      = "segment_id"

  attribute {
    name = "video_id"
    type = "S"
  }

  attribute {
    name = "segment_id"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  tags = { Name = "${var.project_name}-segments" }
}

# DynamoDB table for entities
resource "aws_dynamodb_table" "entities" {
  name           = "${var.project_name}-entities"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "entity_id"

  attribute {
    name = "entity_id"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  tags = { Name = "${var.project_name}-entities" }
}

# Projects table
resource "aws_dynamodb_table" "projects" {
  name         = "${var.project_name}-projects"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "project_id"

  attribute {
    name = "project_id"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  global_secondary_index {
    name            = "user_id-index"
    hash_key        = "user_id"
    projection_type = "ALL"
  }
}
