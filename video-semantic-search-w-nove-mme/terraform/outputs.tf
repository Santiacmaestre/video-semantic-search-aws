output "api_endpoint" {
  description = "API Gateway endpoint URL"
  value       = aws_api_gateway_stage.prod.invoke_url
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "Cognito App Client ID"
  value       = aws_cognito_user_pool_client.main.id
}

output "cognito_domain" {
  description = "Cognito hosted UI domain"
  value       = aws_cognito_user_pool_domain.main.domain
}

output "static_website_bucket" {
  description = "S3 bucket for static website"
  value       = aws_s3_bucket.static_website.id
}

output "video_bucket" {
  description = "S3 bucket for videos"
  value       = aws_s3_bucket.videos.id
}

output "cloudfront_static_domain" {
  description = "CloudFront domain for static website"
  value       = aws_cloudfront_distribution.static_website.domain_name
}

output "cloudfront_video_domain" {
  description = "CloudFront domain for videos"
  value       = aws_cloudfront_distribution.videos.domain_name
}

output "vector_bucket_name" {
  description = "S3 Vectors bucket name"
  value       = local.vector_bucket_name
}

output "ecr_repository_url" {
  description = "ECR repository URL for worker Lambda"
  value       = aws_ecr_repository.worker.repository_url
}

output "worker_lambda_name" {
  description = "ECR repository URL for Docker-based Lambdas"
  value       = aws_ecr_repository.worker.repository_url
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.video_processing.arn
}

output "opensearch_endpoint" {
  description = "OpenSearch managed domain endpoint"
  value       = aws_opensearch_domain.segments.endpoint
}

output "opensearch_domain_arn" {
  description = "OpenSearch managed domain ARN"
  value       = aws_opensearch_domain.segments.arn
}
