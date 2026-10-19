"""Small AWS helpers shared by the handlers: store a report, send a notification, publish metrics."""
import json

NAMESPACE = "FinOps"


def store_report(s3, bucket, prefix, data, html_text, now):
    """Writes <prefix>/<date>.json, <prefix>/latest.json and <prefix>/latest.html to the reports bucket."""
    if not bucket:
        return []
    body = json.dumps(data, indent=2, default=str).encode()
    keys = [f"{prefix}/{now.date().isoformat()}.json", f"{prefix}/latest.json"]
    for key in keys:
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    s3.put_object(Bucket=bucket, Key=f"{prefix}/latest.html", Body=html_text.encode(), ContentType="text/html")
    return keys + [f"{prefix}/latest.html"]


def publish(sns, topic_arn, subject, message):
    if not topic_arn:
        return False
    sns.publish(TopicArn=topic_arn, Subject=subject[:99], Message=message[:250000])
    return True


def put_metrics(cloudwatch, metrics):
    """metrics: list of (name, value, unit)."""
    cloudwatch.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[{"MetricName": n, "Value": float(v), "Unit": u} for n, v, u in metrics],
    )
