variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use"
  type        = string
  default     = "default"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "video-search-v2"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

variable "lambda_runtime" {
  description = "Lambda runtime version"
  type        = string
  default     = "python3.13"
}

variable "lambda_memory" {
  description = "Lambda memory allocation in MB"
  type        = number
  default     = 512
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 30
}

variable "cognito_email_from" {
  description = "Email address for Cognito notifications"
  type        = string
  default     = "noreply@example.com"
}

variable "deploy_id" {
  description = "Unique suffix for OpenSearch domain (avoids name collision during redeploy)"
  type        = string
  default     = ""
}
