#!/usr/bin/env bash
# Deploy everything. Required: ALERT_EMAIL. Optional: AWS_REGION (default us-east-1), MONTHLY_BUDGET_USD (default 50).
set -euo pipefail
cd "$(dirname "$0")/../terraform"
: "${ALERT_EMAIL:?Set ALERT_EMAIL, e.g. export ALERT_EMAIL=you@example.com}"

terraform init -input=false
terraform apply -input=false -auto-approve \
  -var "region=${AWS_REGION:-us-east-1}" \
  -var "alert_email=${ALERT_EMAIL}" \
  -var "monthly_budget_usd=${MONTHLY_BUDGET_USD:-50}"
echo
terraform output
echo
echo "Check your inbox and CONFIRM the SNS subscription email, or you will not receive alerts."
