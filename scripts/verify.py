"""End-to-end check against the deployed stack. Run after deploy.sh and demo_resources.py create.

Invokes the three Lambdas for real and checks the results:
  scanner   -> finds the orphan volume + unattached EIP + untagged resources, writes S3 report, publishes metrics
  scheduler -> stops the Schedule=office-hours demo instance, then starts it again
  reporter  -> returns a cost report (Cost Explorer may say 'unavailable' on a brand-new account; that is reported, not an error)
"""
import json
import os
import sys
import time

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
PROJECT = os.environ.get("PROJECT", "finops")
lam = boto3.client("lambda", region_name=REGION)
ec2 = boto3.client("ec2", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
failures = []


def check(ok, label):
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


def invoke(name, payload=None):
    resp = lam.invoke(FunctionName=f"{PROJECT}-{name}", Payload=json.dumps(payload or {}).encode())
    body = json.loads(resp["Payload"].read())
    if resp.get("FunctionError"):
        print(json.dumps(body, indent=2))
    check(not resp.get("FunctionError"), f"{name} ran without error")
    return body


def demo_instance():
    r = ec2.describe_instances(Filters=[{"Name": "tag:finops-demo", "Values": ["true"]},
                                        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
    return r["Reservations"][0]["Instances"][0]["InstanceId"]


def wait(iid, waiter):
    ec2.get_waiter(waiter).wait(InstanceIds=[iid], WaiterConfig={"Delay": 5, "MaxAttempts": 60})


iid = demo_instance()
wait(iid, "instance_running")

print("\n-- scanner")
scan = invoke("scanner")
kinds = {f["kind"] for f in scan.get("findings", [])}
check({"unattached_volume", "unassociated_ip"} <= kinds, f"scanner found orphan volume and Elastic IP (saw {sorted(kinds)})")
check(any(n["id"] == iid for n in scan.get("non_compliant", [])), "scanner flagged the untagged demo instance")
check(scan["summary"]["estimated_monthly_waste_usd"] > 0, f"estimated waste ${scan['summary']['estimated_monthly_waste_usd']}/month")
bucket = scan["report_keys"][0] and next(b["Name"] for b in s3.list_buckets()["Buckets"] if b["Name"].startswith(f"{PROJECT}-reports-"))
head = s3.head_object(Bucket=bucket, Key="scans/latest.html")
check(head["ContentLength"] > 0, f"HTML report written to s3://{bucket}/scans/latest.html")

print("\n-- scheduler")
check(iid in invoke("scheduler", {"action": "stop"})["acted_on"], "stop action selected the demo instance")
wait(iid, "instance_stopped")
check(True, "demo instance is stopped")
check(iid in invoke("scheduler", {"action": "start"})["acted_on"], "start action selected the demo instance")
wait(iid, "instance_running")
check(True, "demo instance is running again")

print("\n-- reporter")
rep = invoke("reporter")
s = rep.get("summary", {})
check("month_to_date_usd" in s, f"cost report produced (month to date ${s.get('month_to_date_usd')}; notes: {s.get('notes')})")

print("\n-- metrics (can take a minute to appear)")
cw = boto3.client("cloudwatch", region_name=REGION)
for _ in range(12):
    names = {m["MetricName"] for m in cw.list_metrics(Namespace="FinOps")["Metrics"]}
    if "EstimatedMonthlyWasteUSD" in names:
        break
    time.sleep(10)
check("EstimatedMonthlyWasteUSD" in names, "FinOps metrics are in CloudWatch")

print()
sys.exit(1 if failures else 0)
