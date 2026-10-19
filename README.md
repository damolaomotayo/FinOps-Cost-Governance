# FinOps and Cloud Cost Governance on AWS

Automated cost control: **see** spend, **catch** waste, **enforce** tagging and sizes, **cut** non-production runtime.
Run-from-scratch commands are in [STEPS.md](STEPS.md).

```
Budgets (80% actual, 100% forecast) ─┐
Cost Anomaly Detection ──────────────┼─► SNS topic ─► email
Alarm: estimated waste > $10 ────────┘        ▲
EventBridge ─► scanner Lambda  ─► S3 reports + FinOps metrics ─► CloudWatch dashboard
           ─► reporter Lambda (weekly Cost Explorer report) ─────┘
           ─► scheduler Lambda: stop dev instances 18:00 UTC, start 06:00 UTC Mon-Fri
IAM guardrail policy: no EC2 launch without Owner/Project/Environment tags; only small instance types
```

## What each piece does

| Piece | Purpose |
|---|---|
| Budgets + anomaly detection | Native AWS alerts: money going over plan, or an unusual jump in a service. |
| Scanner (daily) | Finds unattached EBS volumes, unassociated Elastic IPs, old snapshots, long-stopped instances, idle instances; checks required tags on EC2, EBS, S3. Estimates the monthly waste. Report-only: never deletes. |
| Reporter (weekly) | Month to date (gross, before credits), credits, forecast, spend per `Project` tag, services that spiked vs their 7-day average. |
| Scheduler | Stops instances tagged `Schedule=office-hours` outside work hours (about 70% compute saving). Never touches `Environment=prod` or Auto Scaling instances. |
| Guardrail policy | `policy/ec2-guardrails.json`: denies launching untagged instances and large instance types. Tested with the IAM policy simulator. |

## Design decisions worth explaining in an interview

- **Detect, don't delete.** The scanner only reports; deleting someone's volume automatically is a bigger risk than the savings.
- **Exemptions are explicit.** `finops:ignore=true` skips a resource, so exceptions are visible in tags, not hidden in code.
- **Least privilege per Lambda.** The scheduler can only stop/start instances carrying the `Schedule` tag (IAM condition), the scanner is read-only, the reporter only reads Cost Explorer.
- **Graceful degradation.** Each Cost Explorer query fails independently; a new account gets a report with notes rather than an error.
- **Honest numbers.** Costs are estimates from list prices and labelled so; credits are shown separately from gross spend.
- **Tags drive everything.** Cost allocation by `Project` only works if tags exist, which is why tagging is enforced at launch and audited daily.

## Limits

- Scans one region per deployment. Run the stack per region for more.
- Cost Explorer data lags up to a day. Each API call costs $0.01.
- Tag-on-launch enforcement via IAM applies to the principals the policy is attached to; for org-wide enforcement use an SCP or tag policies.

## Tests

`pytest` covers the pure logic, the scanner and scheduler against mocked AWS (moto), and the reporter with a fake Cost Explorer.
