variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "finops"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "owner" {
  type    = string
  default = "platform"
}

variable "alert_email" {
  type        = string
  description = "Receives budget, anomaly, scan and cost report emails. You must confirm the SNS subscription email."
}

variable "monthly_budget_usd" {
  type    = number
  default = 50
}

variable "anomaly_threshold_usd" {
  type        = number
  default     = 5
  description = "Alert on anomalies whose total impact is at least this many USD."
}

variable "required_tags" {
  type    = list(string)
  default = ["Owner", "Project", "Environment"]
}

variable "snapshot_max_age_days" {
  type    = number
  default = 90
}

variable "stopped_max_days" {
  type    = number
  default = 14
}

variable "min_idle_age_days" {
  type        = number
  default     = 0
  description = "Ignore unattached volumes younger than this. Keep 0 for the demo; use 7 in real life."
}

variable "waste_alarm_usd" {
  type    = number
  default = 10
}

variable "scan_schedule" {
  type    = string
  default = "cron(0 6 * * ? *)"
}

variable "report_schedule" {
  type    = string
  default = "cron(0 7 ? * MON *)"
}

variable "office_start_hour_utc" {
  type    = number
  default = 6
}

variable "office_end_hour_utc" {
  type    = number
  default = 18
}
