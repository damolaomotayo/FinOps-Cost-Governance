"""Scanner Lambda: finds wasted spend and untagged resources, then reports them.

Report-only by design. It never deletes or changes anything: a human decides.
Waste detected:
  unattached EBS volumes, unassociated Elastic IPs, old snapshots (not used by an AMI),
  long-stopped instances (their disks still cost money), idle running instances (low CPU).
Governance checked: required tags (Owner, Project, Environment by default) on EC2 instances,
EBS volumes and S3 buckets. Put finops:ignore=true on a resource to exempt it.
"""
import os
import re
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

import awsio
import core


def load_config(env=None):
    env = os.environ if env is None else env
    return {
        "required_tags": [t.strip() for t in env.get("REQUIRED_TAGS", "Owner,Project,Environment").split(",") if t.strip()],
        "snapshot_max_age_days": int(env.get("SNAPSHOT_MAX_AGE_DAYS", "90")),
        "stopped_max_days": int(env.get("STOPPED_MAX_DAYS", "14")),
        "idle_cpu_threshold": float(env.get("IDLE_CPU_THRESHOLD", "5")),
        "min_idle_age_days": int(env.get("MIN_IDLE_AGE_DAYS", "0")),
        "report_bucket": env.get("REPORT_BUCKET", ""),
        "topic_arn": env.get("TOPIC_ARN", ""),
        "region": env.get("AWS_REGION", env.get("AWS_DEFAULT_REGION", "")),
    }


# --- waste detection ---------------------------------------------------------------

def find_unattached_volumes(ec2, cfg, now):
    out = []
    pages = ec2.get_paginator("describe_volumes").paginate(Filters=[{"Name": "status", "Values": ["available"]}])
    for page in pages:
        for vol in page["Volumes"]:
            tags = core.tags_to_dict(vol.get("Tags"))
            age = (now - vol["CreateTime"]).days
            if core.is_ignored(tags) or age < cfg["min_idle_age_days"]:
                continue
            out.append(core.Finding(
                "unattached_volume", vol["VolumeId"],
                f"{vol['Size']} GiB {vol['VolumeType']}, created {age} days ago, not attached to any instance",
                core.volume_monthly_cost(vol["Size"], vol["VolumeType"]),
                "Snapshot it if the data matters, then delete it.", cfg["region"]))
    return out


def find_unassociated_ips(ec2, cfg):
    out = []
    for addr in ec2.describe_addresses()["Addresses"]:
        if "AssociationId" in addr or core.is_ignored(core.tags_to_dict(addr.get("Tags"))):
            continue
        ident = addr.get("AllocationId") or addr.get("PublicIp", "unknown")
        out.append(core.Finding(
            "unassociated_ip", ident, f"Elastic IP {addr.get('PublicIp', '')} is not attached to anything",
            core.public_ip_monthly_cost(), "Release it.", cfg["region"]))
    return out


def find_old_snapshots(ec2, cfg, now):
    ami_snapshots = set()
    for image in ec2.describe_images(Owners=["self"])["Images"]:
        for mapping in image.get("BlockDeviceMappings", []):
            if mapping.get("Ebs", {}).get("SnapshotId"):
                ami_snapshots.add(mapping["Ebs"]["SnapshotId"])
    cutoff = now - timedelta(days=cfg["snapshot_max_age_days"])
    out = []
    for page in ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"]):
        for snap in page["Snapshots"]:
            if snap["SnapshotId"] in ami_snapshots or snap["StartTime"] > cutoff:
                continue
            if core.is_ignored(core.tags_to_dict(snap.get("Tags"))):
                continue
            age = (now - snap["StartTime"]).days
            out.append(core.Finding(
                "old_snapshot", snap["SnapshotId"],
                f"{snap['VolumeSize']} GiB snapshot, {age} days old, not used by any AMI (cost is an upper bound)",
                core.snapshot_monthly_cost(snap["VolumeSize"]),
                "Delete it, or add a lifecycle policy (Data Lifecycle Manager).", cfg["region"]))
    return out


def find_stopped_instances(ec2, cfg, now):
    out = []
    pages = ec2.get_paginator("describe_instances").paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}])
    for page in pages:
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                if core.is_ignored(core.tags_to_dict(inst.get("Tags"))):
                    continue
                stopped_at = core.parse_stop_time(inst.get("StateTransitionReason"))
                if stopped_at is None:
                    continue
                days = (now - stopped_at).days
                if days < cfg["stopped_max_days"]:
                    continue
                volume_ids = [m["Ebs"]["VolumeId"] for m in inst.get("BlockDeviceMappings", []) if "Ebs" in m]
                cost = 0.0
                if volume_ids:
                    for vol in ec2.describe_volumes(VolumeIds=volume_ids)["Volumes"]:
                        cost += core.volume_monthly_cost(vol["Size"], vol["VolumeType"])
                out.append(core.Finding(
                    "stopped_instance", inst["InstanceId"],
                    f"{inst['InstanceType']} stopped for {days} days; its disks still cost money",
                    round(cost, 2), "Terminate it (snapshot or create an AMI first if needed).", cfg["region"]))
    return out


def find_idle_instances(ec2, cloudwatch, cfg, now):
    """Running for 7+ days and daily average CPU below the threshold on every day."""
    out = []
    pages = ec2.get_paginator("describe_instances").paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}])
    for page in pages:
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                if core.is_ignored(core.tags_to_dict(inst.get("Tags"))):
                    continue
                if (now - inst["LaunchTime"]).days < 7:
                    continue
                stats = cloudwatch.get_metric_statistics(
                    Namespace="AWS/EC2", MetricName="CPUUtilization",
                    Dimensions=[{"Name": "InstanceId", "Value": inst["InstanceId"]}],
                    StartTime=now - timedelta(days=7), EndTime=now, Period=86400, Statistics=["Average"])
                points = [p["Average"] for p in stats.get("Datapoints", [])]
                if len(points) >= 5 and max(points) < cfg["idle_cpu_threshold"]:
                    out.append(core.Finding(
                        "idle_instance", inst["InstanceId"],
                        f"{inst['InstanceType']} averaged under {cfg['idle_cpu_threshold']}% CPU every day for the last 7 days",
                        core.instance_monthly_cost(inst["InstanceType"]),
                        "Downsize it, schedule it, or stop it.", cfg["region"]))
    return out


def scan_waste(ec2, cloudwatch, cfg, now):
    return (find_unattached_volumes(ec2, cfg, now) + find_unassociated_ips(ec2, cfg)
            + find_old_snapshots(ec2, cfg, now) + find_stopped_instances(ec2, cfg, now)
            + find_idle_instances(ec2, cloudwatch, cfg, now))


# --- tag compliance ---------------------------------------------------------------

def scan_tags(ec2, s3, cfg):
    required = cfg["required_tags"]
    non_compliant, checked = [], {"ec2_instance": 0, "ebs_volume": 0, "s3_bucket": 0}

    def check(kind, ident, tag_list):
        tags = core.tags_to_dict(tag_list)
        if core.is_ignored(tags):
            return
        checked[kind] += 1
        missing = core.missing_tags(tags, required)
        if missing:
            non_compliant.append({"type": kind, "id": ident, "name": tags.get("Name", ""), "missing": missing})

    for page in ec2.get_paginator("describe_instances").paginate():
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                if inst["State"]["Name"] not in ("terminated", "shutting-down"):
                    check("ec2_instance", inst["InstanceId"], inst.get("Tags"))
    for page in ec2.get_paginator("describe_volumes").paginate():
        for vol in page["Volumes"]:
            check("ebs_volume", vol["VolumeId"], vol.get("Tags"))
    for bucket in s3.list_buckets().get("Buckets", []):
        try:
            tag_list = s3.get_bucket_tagging(Bucket=bucket["Name"])["TagSet"]
        except ClientError as err:
            if err.response["Error"]["Code"] != "NoSuchTagSet":
                continue  # bucket in another region or not readable: skip rather than guess
            tag_list = []
        check("s3_bucket", bucket["Name"], tag_list)
    return non_compliant, checked


# --- Lambda entry point --------------------------------------------------------------

def handler(event, context):
    cfg = load_config()
    now = datetime.now(timezone.utc)
    ec2, cloudwatch, s3, sns = (boto3.client(n) for n in ("ec2", "cloudwatch", "s3", "sns"))

    findings = scan_waste(ec2, cloudwatch, cfg, now)
    non_compliant, checked = scan_tags(ec2, s3, cfg)
    report = core.build_scan_report(findings, non_compliant, checked, now, cfg["region"])
    summary = report["summary"]

    keys = awsio.store_report(s3, cfg["report_bucket"], "scans", report, core.render_scan_html(report), now)
    awsio.put_metrics(cloudwatch, [
        ("EstimatedMonthlyWasteUSD", summary["estimated_monthly_waste_usd"], "None"),
        ("WasteFindings", summary["findings_count"], "Count"),
        ("NonCompliantResources", summary["non_compliant_count"], "Count"),
        ("TagCompliancePercent", summary["compliance_pct"], "Percent"),
    ])
    subject = (f"FinOps scan: {summary['findings_count']} findings, ~${summary['estimated_monthly_waste_usd']:.0f}/month waste, "
               f"{summary['compliance_pct']}% tagged")
    notified = awsio.publish(sns, cfg["topic_arn"], subject, core.render_scan_text(report))

    return {"summary": summary, "findings": report["findings"][:500], "non_compliant": non_compliant[:500],
            "report_keys": keys, "notified": notified}
