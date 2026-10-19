"""Creates (or deletes) deliberately wasteful, untagged demo resources so the scanner has something to find.

  python scripts/demo_resources.py create   # orphan 1 GiB volume, unattached Elastic IP, t4g.nano tagged Schedule=office-hours
  python scripts/demo_resources.py delete   # removes everything tagged finops-demo=true

Cost: pennies per day. Always run 'delete' (destroy.sh does it for you).
"""
import os
import sys

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
DEMO_TAG = {"Key": "finops-demo", "Value": "true"}
ec2 = boto3.client("ec2", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)


def tag_spec(kind, extra=()):
    return [{"ResourceType": kind, "Tags": [DEMO_TAG, *extra]}]


def create():
    az = ec2.describe_availability_zones()["AvailabilityZones"][0]["ZoneName"]
    vol = ec2.create_volume(AvailabilityZone=az, Size=1, VolumeType="gp3", TagSpecifications=tag_spec("volume"))
    print("orphan volume:", vol["VolumeId"])
    eip = ec2.allocate_address(Domain="vpc", TagSpecifications=tag_spec("elastic-ip"))
    print("unattached EIP:", eip["AllocationId"])
    ami = ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64")["Parameter"]["Value"]
    inst = ec2.run_instances(
        ImageId=ami, InstanceType="t4g.nano", MinCount=1, MaxCount=1,
        TagSpecifications=tag_spec("instance", [{"Key": "Schedule", "Value": "office-hours"}, {"Key": "Environment", "Value": "dev"},
                                                {"Key": "Name", "Value": "finops-demo"}]))
    print("scheduled instance:", inst["Instances"][0]["InstanceId"], "(no Owner/Project tag on purpose)")


def delete():
    flt = [{"Name": "tag:finops-demo", "Values": ["true"]}]
    ids = [i["InstanceId"] for r in ec2.describe_instances(Filters=flt + [{"Name": "instance-state-name", "Values": [
        "pending", "running", "stopping", "stopped"]}])["Reservations"] for i in r["Instances"]]
    if ids:
        ec2.terminate_instances(InstanceIds=ids)
        ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)
        print("terminated", ids)
    for a in ec2.describe_addresses(Filters=flt)["Addresses"]:
        ec2.release_address(AllocationId=a["AllocationId"]); print("released", a["AllocationId"])
    for v in ec2.describe_volumes(Filters=flt)["Volumes"]:
        ec2.delete_volume(VolumeId=v["VolumeId"]); print("deleted", v["VolumeId"])


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("create", "delete"):
        sys.exit(__doc__)
    {"create": create, "delete": delete}[sys.argv[1]]()
