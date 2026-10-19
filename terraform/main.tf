terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.60" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = var.project, Environment = var.environment, Owner = var.owner, ManagedBy = "terraform" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  name       = var.project
  account_id = data.aws_caller_identity.current.account_id
}

# ---------------------------------------------------------------- notifications

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Budgets and Cost Anomaly Detection are AWS services: they need permission to publish to the topic.
data "aws_iam_policy_document" "topic" {
  statement {
    sid       = "AllowBudgetsAndAnomalyDetection"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com", "costalerts.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.topic.json
}

# ---------------------------------------------------------------- budgets + anomaly detection

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.alerts.arn]
  }

  depends_on = [aws_sns_topic_policy.alerts]
}

resource "aws_ce_anomaly_monitor" "services" {
  name              = "${local.name}-services"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "immediate" {
  name             = "${local.name}-anomalies"
  frequency        = "IMMEDIATE"
  monitor_arn_list = [aws_ce_anomaly_monitor.services.arn]

  subscriber {
    type    = "SNS"
    address = aws_sns_topic.alerts.arn
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = [tostring(var.anomaly_threshold_usd)]
    }
  }

  depends_on = [aws_sns_topic_policy.alerts]
}

# ---------------------------------------------------------------- reports bucket

resource "aws_s3_bucket" "reports" {
  bucket        = "${local.name}-reports-${local.account_id}-${var.region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    id     = "expire-old-reports"
    status = "Enabled"
    filter {}
    expiration { days = 180 }
  }
}

# ---------------------------------------------------------------- lambdas

data "archive_file" "src" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/.build/src.zip"
  excludes    = ["__pycache__", "*.pyc"]
}

locals {
  common_env = {
    REPORT_BUCKET = aws_s3_bucket.reports.id
    TOPIC_ARN     = aws_sns_topic.alerts.arn
  }
  functions = {
    scanner = {
      handler = "scanner.handler"
      timeout = 300
      memory  = 256
      env = {
        REQUIRED_TAGS         = join(",", var.required_tags)
        SNAPSHOT_MAX_AGE_DAYS = tostring(var.snapshot_max_age_days)
        STOPPED_MAX_DAYS      = tostring(var.stopped_max_days)
        MIN_IDLE_AGE_DAYS     = tostring(var.min_idle_age_days)
      }
    }
    reporter = {
      handler = "reporter.handler"
      timeout = 120
      memory  = 256
      env     = { COST_TAG_KEY = "Project" }
    }
    scheduler = {
      handler = "scheduler.handler"
      timeout = 60
      memory  = 128
      env     = { PROTECTED_ENVIRONMENTS = "prod,production" }
    }
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = 14
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "${local.name}-${each.key}"
  role             = aws_iam_role.fn[each.key].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = each.value.handler
  timeout          = each.value.timeout
  memory_size      = each.value.memory
  filename         = data.archive_file.src.output_path
  source_code_hash = data.archive_file.src.output_base64sha256

  environment {
    variables = merge(local.common_env, each.value.env)
  }

  depends_on = [aws_cloudwatch_log_group.fn]
}

# ---------------------------------------------------------------- schedules

locals {
  schedules = {
    scanner-daily = { fn = "scanner", expr = var.scan_schedule, input = "{}" }
    reporter      = { fn = "reporter", expr = var.report_schedule, input = "{}" }
    stop          = { fn = "scheduler", expr = "cron(0 ${var.office_end_hour_utc} ? * MON-FRI *)", input = jsonencode({ action = "stop" }) }
    start         = { fn = "scheduler", expr = "cron(0 ${var.office_start_hour_utc} ? * MON-FRI *)", input = jsonencode({ action = "start" }) }
  }
}

resource "aws_cloudwatch_event_rule" "schedule" {
  for_each            = local.schedules
  name                = "${local.name}-${each.key}"
  schedule_expression = each.value.expr
}

resource "aws_cloudwatch_event_target" "schedule" {
  for_each = local.schedules
  rule     = aws_cloudwatch_event_rule.schedule[each.key].name
  arn      = aws_lambda_function.fn[each.value.fn].arn
  input    = each.value.input
}

resource "aws_lambda_permission" "schedule" {
  for_each      = local.schedules
  statement_id  = "AllowEventBridge-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn[each.value.fn].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule[each.key].arn
}

# ---------------------------------------------------------------- alarm + dashboard

resource "aws_cloudwatch_metric_alarm" "waste" {
  alarm_name          = "${local.name}-waste-high"
  alarm_description   = "Estimated monthly waste found by the scanner is above the threshold."
  namespace           = "FinOps"
  metric_name         = "EstimatedMonthlyWasteUSD"
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.waste_alarm_usd
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_dashboard" "finops" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title = "Estimated monthly waste (USD)", region = var.region, stat = "Maximum", period = 86400
          metrics = [["FinOps", "EstimatedMonthlyWasteUSD"]]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title = "Tag compliance (%)", region = var.region, stat = "Minimum", period = 86400
          metrics = [["FinOps", "TagCompliancePercent"]]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title = "Findings and untagged resources", region = var.region, stat = "Maximum", period = 86400
          metrics = [["FinOps", "WasteFindings"], ["FinOps", "NonCompliantResources"]]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title = "Office-hours scheduler", region = var.region, stat = "Sum", period = 86400
          metrics = [["FinOps", "InstancesStopped"], ["FinOps", "InstancesStarted"]]
        }
      }
    ]
  })
}
