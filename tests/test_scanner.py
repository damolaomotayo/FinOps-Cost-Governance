import json
from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

import scanner

TAGS = [{"Key": "Owner", "Value": "akin"}, {"Key": "Project", "Value": "demo"}, {"Key": "Environment", "Value": "dev"}]


@pytest.fixture
def aws():
    with mock_aws():
        yield


def run_handler(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return scanner.handler({}, None)


def test_finds_orphans_and_untagged(aws, monkeypatch):
    ec2 = boto3.client("ec2")
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=10, VolumeType="gp3")
    ec2.allocate_address(Domain="vpc")
    out = run_handler(monkeypatch)
    kinds = {f["kind"] for f in out["findings"]}
    assert {"unattached_volume", "unassociated_ip"} <= kinds
    assert out["summary"]["estimated_monthly_waste_usd"] == round(10 * 0.08 + 3.65, 2)
    assert any(n["type"] == "ebs_volume" and n["missing"] == ["Owner", "Project", "Environment"] for n in out["non_compliant"])


def test_ignore_tag_exempts(aws, monkeypatch):
    ec2 = boto3.client("ec2")
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=10, TagSpecifications=[
        {"ResourceType": "volume", "Tags": [{"Key": "finops:ignore", "Value": "true"}]}])
    out = run_handler(monkeypatch)
    assert out["findings"] == [] and out["non_compliant"] == []


def test_min_idle_age_hides_fresh_volumes(aws, monkeypatch):
    boto3.client("ec2").create_volume(AvailabilityZone="us-east-1a", Size=10)
    assert run_handler(monkeypatch, MIN_IDLE_AGE_DAYS="1")["findings"] == []


def test_fully_tagged_volume_is_compliant(aws, monkeypatch):
    boto3.client("ec2").create_volume(AvailabilityZone="us-east-1a", Size=10, TagSpecifications=[
        {"ResourceType": "volume", "Tags": TAGS}])
    out = run_handler(monkeypatch)
    assert out["non_compliant"] == [] and out["summary"]["compliance_pct"] == 100.0


def test_s3_bucket_tag_check(aws, monkeypatch):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="untagged-bucket")
    s3.create_bucket(Bucket="tagged-bucket")
    s3.put_bucket_tagging(Bucket="tagged-bucket", Tagging={"TagSet": TAGS})
    out = run_handler(monkeypatch)
    ids = {n["id"] for n in out["non_compliant"]}
    assert "untagged-bucket" in ids and "tagged-bucket" not in ids


def test_writes_report_and_metrics_and_notifies(aws, monkeypatch):
    s3, sns = boto3.client("s3"), boto3.client("sns")
    s3.create_bucket(Bucket="reports")
    topic = sns.create_topic(Name="t")["TopicArn"]
    boto3.client("ec2").allocate_address(Domain="vpc")
    out = run_handler(monkeypatch, REPORT_BUCKET="reports", TOPIC_ARN=topic)
    assert out["notified"] is True
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket="reports")["Contents"]}
    assert {"scans/latest.json", "scans/latest.html"} <= keys
    body = json.loads(s3.get_object(Bucket="reports", Key="scans/latest.json")["Body"].read())
    assert body["summary"]["findings_count"] == 1
    metrics = boto3.client("cloudwatch").list_metrics(Namespace="FinOps")["Metrics"]
    assert "EstimatedMonthlyWasteUSD" in {m["MetricName"] for m in metrics}


def test_old_snapshot_not_used_by_ami(aws):
    ec2 = boto3.client("ec2")
    vol = ec2.create_volume(AvailabilityZone="us-east-1a", Size=20)["VolumeId"]
    mine = ec2.create_snapshot(VolumeId=vol)["SnapshotId"]
    cfg = scanner.load_config({"SNAPSHOT_MAX_AGE_DAYS": "0", "AWS_REGION": "us-east-1"})
    future = datetime(2100, 1, 1, tzinfo=timezone.utc)  # makes the snapshot look old
    found = [f for f in scanner.find_old_snapshots(ec2, cfg, future) if f.resource_id == mine]  # moto ships preloaded snapshots
    assert len(found) == 1 and found[0].monthly_cost_usd == 1.0
