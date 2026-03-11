# Local variables
locals {
  account_id         = data.aws_caller_identity.current.account_id
  vector_bucket_name = "${var.project_name}-vectors-${local.account_id}"
  video_bucket_name  = "${var.project_name}-videos-${local.account_id}"
  static_bucket_name = "${var.project_name}-static-${local.account_id}"
}

# S3 bucket for videos
resource "aws_s3_bucket" "videos" {
  bucket = local.video_bucket_name
}

resource "aws_s3_bucket_versioning" "videos" {
  bucket = aws_s3_bucket.videos.id
  
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "videos" {
  bucket = aws_s3_bucket.videos.id

  rule {
    id     = "cleanup-temp-files"
    status = "Enabled"

    filter {
      prefix = "frames/"
    }

    expiration {
      days = 30
    }
  }

  rule {
    id     = "cleanup-face-crops"
    status = "Enabled"

    filter {
      prefix = "faces/"
    }

    expiration {
      days = 30
    }
  }
}

resource "aws_s3_bucket_public_access_block" "videos" {
  bucket = aws_s3_bucket.videos.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_cors_configuration" "videos" {
  bucket = aws_s3_bucket.videos.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "PUT", "POST"]
    allowed_origins = ["https://${aws_cloudfront_distribution.static_website.domain_name}"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}

# S3 bucket for static website
resource "aws_s3_bucket" "static_website" {
  bucket = local.static_bucket_name
}

resource "aws_s3_bucket_public_access_block" "static_website" {
  bucket = aws_s3_bucket.static_website.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 bucket policy for CloudFront OAC (videos)
resource "aws_s3_bucket_policy" "videos" {
  bucket = aws_s3_bucket.videos.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudFrontServicePrincipal"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.videos.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.videos.arn
          }
        }
      }
    ]
  })
}

# S3 bucket policy for CloudFront OAC (static website)
resource "aws_s3_bucket_policy" "static_website" {
  bucket = aws_s3_bucket.static_website.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudFrontServicePrincipal"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.static_website.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.static_website.arn
          }
        }
      }
    ]
  })
}

# Note: S3 Vectors bucket is created via AWS CLI in deployment script
# Terraform doesn't yet support S3 Vectors natively
