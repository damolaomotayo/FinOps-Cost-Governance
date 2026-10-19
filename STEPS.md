# STEPS: FinOps and Cloud Cost Governance

## Prerequisites (do these first)

| Need | Details |
|---|---|
| Machine | Any Linux box: EC2 `t3.small` (2 GB), 10 GB disk, Ubuntu 22.04+ or Amazon Linux 2023. |
| AWS permissions | An IAM role on the instance (or `aws configure`) with **AdministratorAccess**. It creates IAM roles, Lambdas, S3, SNS, Budgets, Cost Anomaly Detection. |
| Region | Default `us-east-1`. Same region for everything (`export AWS_REGION=...` to change). |
| Default VPC | Needed only by the demo instance. Check: `aws ec2 describe-vpcs --filters Name=isDefault,Values=true`. |
| Cost Explorer | Billing console → Cost Explorer → **Enable** (do it now; data appears after about 24 hours). Without it the cost report says "unavailable" instead of failing. |
| Cost allocation tag (optional) | Billing console → Cost allocation tags → activate **Project**. Takes up to 24 hours. Fills the "spend by Project" section. |
| Email | An inbox you can read. You must click the SNS confirmation link AWS sends. |
| GitHub | Private repo for this project (git clone URL). |
| Cost | Stack is near free. Each Cost Explorer call is $0.01 (4 per report). Demo resources: about a few cents per day; deleted at the end. |

## Run it (copy and paste)

```bash
# 1. Clone and install everything (git, python3, Terraform 1.9.8, AWS CLI, venv)
git clone <YOUR_REPO_URL> finops && cd finops
bash scripts/setup.sh

# 2. Prove the logic works locally (no AWS calls)
.venv/bin/python -m pytest -q          # expect: 26 passed

# 3. Prove the IAM guardrail policy (no resources created)
.venv/bin/python scripts/verify_guardrails.py   # expect: 8 x PASS

# 4. Deploy (then CONFIRM the SNS email in your inbox)
export ALERT_EMAIL=you@example.com
bash scripts/deploy.sh

# 5. Create wasteful demo resources, check the stack end to end
.venv/bin/python scripts/demo_resources.py create
.venv/bin/python scripts/verify.py              # expect: all PASS

# 6. Look at the results
aws s3 cp s3://$(cd terraform && terraform output -raw reports_bucket)/scans/latest.html - | head -30
# Dashboard: CloudWatch console → Dashboards → finops

# 7. Clean up everything
bash scripts/destroy.sh
```

## If something fails

- `terraform apply` says Budgets/anomaly subscription not allowed: your role lacks admin, or the account is a member account without billing access. Ask the payer account.
- `verify.py` says no demo instance: run step 5's `create` first.
- Reporter shows "DataUnavailableException": Cost Explorer was only just enabled. Wait a day.
- `AccessDenied` on `ce:*`: for member accounts the management account must allow IAM access to billing.
- Anomaly emails never arrive: confirm the SNS subscription email.
