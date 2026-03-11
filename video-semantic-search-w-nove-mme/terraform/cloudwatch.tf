# CloudWatch Log Groups
resource "aws_cloudwatch_log_group" "search_function" {
  name              = "/aws/lambda/${var.project_name}-search-function"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "upload_function" {
  name              = "/aws/lambda/${var.project_name}-upload-function"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "video_function" {
  name              = "/aws/lambda/${var.project_name}-video-function"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "entity_function" {
  name              = "/aws/lambda/${var.project_name}-entity-function"
  retention_in_days = 7
}

# CloudWatch Alarms
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.project_name}-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "Alert when Lambda errors exceed threshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "api_gateway_5xx" {
  alarm_name          = "${var.project_name}-api-5xx-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "5XXError"
  namespace           = "AWS/ApiGateway"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "Alert when API Gateway 5xx errors exceed threshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ApiName = aws_api_gateway_rest_api.main.name
  }
}

resource "aws_cloudwatch_log_group" "project_function" {
  name              = "/aws/lambda/${var.project_name}-project-function"
  retention_in_days = 7
}
