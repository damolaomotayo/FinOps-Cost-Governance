import boto3
import pytest
from moto import mock_aws

import scheduler


def launch(ec2, tags):
    ami = ec2.describe_images()["Images"][0]["ImageId"]
    return ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1, InstanceType="t3.micro", TagSpecifications=[
        {"ResourceType": "instance", "Tags": [{"Key": k, "Value": v} for k, v in tags.items()]}])["Instances"][0]["InstanceId"]


def state(ec2, iid):
    return ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]["State"]["Name"]


@pytest.fixture
def ec2():
    with mock_aws():
        yield boto3.client("ec2")


def test_stop_then_start_only_scheduled_non_prod(ec2):
    dev = launch(ec2, {"Schedule": "office-hours", "Environment": "dev"})
    prod = launch(ec2, {"Schedule": "office-hours", "Environment": "prod"})
    other = launch(ec2, {"Environment": "dev"})

    out = scheduler.handler({"action": "stop"}, None)
    assert out["acted_on"] == [dev] and out["skipped_protected"] == [prod]
    assert (state(ec2, dev), state(ec2, prod), state(ec2, other)) == ("stopped", "running", "running")

    out = scheduler.handler({"action": "start"}, None)
    assert out["acted_on"] == [dev]
    assert state(ec2, dev) == "running"


def test_nothing_to_do_is_fine(ec2):
    assert scheduler.handler({"action": "stop"}, None)["acted_on"] == []


def test_rejects_bad_action(ec2):
    with pytest.raises(ValueError):
        scheduler.handler({"action": "explode"}, None)
    with pytest.raises(ValueError):
        scheduler.handler({}, None)
