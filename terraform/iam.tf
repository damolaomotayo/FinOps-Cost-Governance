data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  for_each           = local.functions
  name               = "${local.name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "logs" {
  for_each   = local.functions
  role       = aws_iam_role.fn[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Shared by all three: write reports, publish to the topic, publish FinOps metrics.
data "aws_iam_policy_document" "common" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.reports.arn}/*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["FinOps"]
    }
  }
}

resource "aws_iam_role_policy" "common" {
  for_each = local.functions
  name     = "common"
  role     = aws_iam_role.fn[each.key].id
  policy   = data.aws_iam_policy_document.common.json
}

# Scanner: read-only.
data "aws_iam_policy_document" "scanner" {
  statement {
    actions = [
      "ec2:DescribeInstances", "ec2:DescribeVolumes", "ec2:DescribeAddresses",
      "ec2:DescribeSnapshots", "ec2:DescribeImages",
      "cloudwatch:GetMetricStatistics",
      "s3:ListAllMyBuckets", "s3:GetBucketTagging",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "scanner" {
  name   = "scanner-read-only"
  role   = aws_iam_role.fn["scanner"].id
  policy = data.aws_iam_policy_document.scanner.json
}

# Reporter: Cost Explorer reads only.
data "aws_iam_policy_document" "reporter" {
  statement {
    actions   = ["ce:GetCostAndUsage", "ce:GetCostForecast"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "reporter" {
  name   = "reporter-cost-explorer"
  role   = aws_iam_role.fn["reporter"].id
  policy = data.aws_iam_policy_document.reporter.json
}

# Scheduler: may only stop/start instances that carry the Schedule tag (least privilege).
data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
  statement {
    actions   = ["ec2:StopInstances", "ec2:StartInstances"]
    resources = ["arn:aws:ec2:*:${local.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Schedule"
      values   = ["office-hours"]
    }
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "scheduler-tagged-instances-only"
  role   = aws_iam_role.fn["scheduler"].id
  policy = data.aws_iam_policy_document.scheduler.json
}
