"""Scheduler Lambda: stops non-production instances outside office hours and starts them again.

Triggered by EventBridge with {"action": "stop"} or {"action": "start"}.
Only instances tagged Schedule=office-hours are touched. Instances whose Environment tag is a protected
value (prod, production by default) and instances managed by an Auto Scaling group are never touched.
Running 10 hours x 5 days instead of 24 x 7 cuts a dev instance's compute bill by about 70%.
"""
import os

import boto3

import awsio
import core


def load_config(env=None):
    env = os.environ if env is None else env
    return {
        "tag_key": env.get("SCHEDULE_TAG_KEY", "Schedule"),
        "tag_value": env.get("SCHEDULE_TAG_VALUE", "office-hours"),
        "protected": {v.strip().lower() for v in env.get("PROTECTED_ENVIRONMENTS", "prod,production").split(",") if v.strip()},
    }


def select_instances(ec2, cfg, state):
    chosen, protected = [], []
    pages = ec2.get_paginator("describe_instances").paginate(Filters=[
        {"Name": f"tag:{cfg['tag_key']}", "Values": [cfg["tag_value"]]},
        {"Name": "instance-state-name", "Values": [state]},
    ])
    for page in pages:
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                tags = core.tags_to_dict(inst.get("Tags"))
                if tags.get("Environment", "").lower() in cfg["protected"] or "aws:autoscaling:groupName" in tags:
                    protected.append(inst["InstanceId"])
                else:
                    chosen.append(inst["InstanceId"])
    return chosen, protected


def handler(event, context):
    action = (event or {}).get("action")
    if action not in ("start", "stop"):
        raise ValueError("event must be {'action': 'start'} or {'action': 'stop'}")
    cfg = load_config()
    ec2 = boto3.client("ec2")

    ids, protected = select_instances(ec2, cfg, "running" if action == "stop" else "stopped")
    if ids:
        (ec2.stop_instances if action == "stop" else ec2.start_instances)(InstanceIds=ids)

    awsio.put_metrics(boto3.client("cloudwatch"), [
        ("InstancesStopped" if action == "stop" else "InstancesStarted", len(ids), "Count")])
    print(f"{action}: acted on {ids}, left alone (protected) {protected}")
    return {"action": action, "acted_on": ids, "skipped_protected": protected}
