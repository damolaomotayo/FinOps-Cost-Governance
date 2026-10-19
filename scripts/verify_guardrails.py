"""Proves the IAM guardrail policy works WITHOUT launching anything, using the IAM policy simulator.

Each case asks: 'with this policy, could someone run ec2:RunInstances with these tags and this instance type?'
Run: .venv/bin/python scripts/verify_guardrails.py
"""
import json
import os
import sys

import boto3

policy = open(os.path.join(os.path.dirname(__file__), "..", "policy", "ec2-guardrails.json")).read()
iam = boto3.client("iam", region_name=os.environ.get("AWS_REGION", "us-east-1"))
GOOD = {"Owner": "akin", "Project": "demo", "Environment": "dev"}


def allowed(tags, instance_type):
    ctx = [{"ContextKeyName": "ec2:InstanceType", "ContextKeyValues": [instance_type], "ContextKeyType": "string"}]
    for k, v in tags.items():
        ctx.append({"ContextKeyName": f"aws:RequestTag/{k}", "ContextKeyValues": [v], "ContextKeyType": "string"})
    resp = iam.simulate_custom_policy(
        PolicyInputList=[policy], ActionNames=["ec2:RunInstances"],
        ResourceArns=["arn:aws:ec2:us-east-1:123456789012:instance/*"], ContextEntries=ctx)
    return resp["EvaluationResults"][0]["EvalDecision"] == "allowed"


cases = [
    ("fully tagged t3.micro", GOOD, "t3.micro", True),
    ("fully tagged t4g.small", GOOD, "t4g.small", True),
    ("missing Owner tag", {k: v for k, v in GOOD.items() if k != "Owner"}, "t3.micro", False),
    ("missing Project tag", {k: v for k, v in GOOD.items() if k != "Project"}, "t3.micro", False),
    ("missing Environment tag", {k: v for k, v in GOOD.items() if k != "Environment"}, "t3.micro", False),
    ("no tags at all", {}, "t3.micro", False),
    ("tagged but m5.24xlarge", GOOD, "m5.24xlarge", False),
    ("tagged but p4d.24xlarge", GOOD, "p4d.24xlarge", False),
]
bad = 0
for label, tags, itype, want in cases:
    got = allowed(tags, itype)
    ok = got == want
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {'allowed' if got else 'denied'} (expected {'allowed' if want else 'denied'})")
sys.exit(1 if bad else 0)
