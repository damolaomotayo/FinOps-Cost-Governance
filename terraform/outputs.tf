output "reports_bucket" {
  value = aws_s3_bucket.reports.id
}

output "topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "function_names" {
  value = { for k, v in aws_lambda_function.fn : k => v.function_name }
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.finops.dashboard_name
}

output "region" {
  value = var.region
}
