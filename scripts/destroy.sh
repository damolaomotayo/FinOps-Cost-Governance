#!/usr/bin/env bash
# Remove demo resources first, then every Terraform resource.
set -euo pipefail
ROOT="$(dirname "$0")/.."
export AWS_REGION="${AWS_REGION:-us-east-1}"
"${ROOT}/.venv/bin/python" "${ROOT}/scripts/demo_resources.py" delete || true
cd "${ROOT}/terraform"
terraform destroy -input=false -auto-approve -var "region=${AWS_REGION}" -var "alert_email=${ALERT_EMAIL:-unused@example.com}"
echo "All resources destroyed."
