"""Reporter Lambda: weekly cost report from Cost Explorer.

Shows month-to-date spend (gross, before credits), credits applied, the month-end forecast, spend per
Project tag, and services whose spend jumped versus their 7-day average.
Each run makes 4 Cost Explorer API calls (AWS charges $0.01 per call).
If Cost Explorer is not enabled yet, or data is not available, the report says so instead of failing.
"""
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

import awsio
import core

NOT_CREDITS = {"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}}


def load_config(env=None):
    env = os.environ if env is None else env
    return {
        "report_bucket": env.get("REPORT_BUCKET", ""),
        "topic_arn": env.get("TOPIC_ARN", ""),
        "tag_key": env.get("COST_TAG_KEY", "Project"),
    }


def _amount(group):
    return float(group["Metrics"]["UnblendedCost"]["Amount"])


def _paged(ce, **kwargs):
    token = None
    while True:
        resp = ce.get_cost_and_usage(**kwargs, **({"NextPageToken": token} if token else {}))
        yield from resp["ResultsByTime"]
        token = resp.get("NextPageToken")
        if not token:
            return


def fetch_daily(ce, start, end):
    rows = []
    for period in _paged(ce, TimePeriod={"Start": start.isoformat(), "End": end.isoformat()}, Granularity="DAILY",
                         Metrics=["UnblendedCost"], Filter=NOT_CREDITS,
                         GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}]):
        for group in period["Groups"]:
            rows.append({"date": period["TimePeriod"]["Start"], "service": group["Keys"][0], "amount": _amount(group)})
    return rows


def fetch_credits(ce, start, end):
    total = 0.0
    for period in _paged(ce, TimePeriod={"Start": start.isoformat(), "End": end.isoformat()}, Granularity="MONTHLY",
                         Metrics=["UnblendedCost"], GroupBy=[{"Type": "DIMENSION", "Key": "RECORD_TYPE"}]):
        for group in period["Groups"]:
            if group["Keys"][0] == "Credit":
                total += _amount(group)
    return total


def fetch_by_tag(ce, tag_key, start, end):
    totals = {}
    for period in _paged(ce, TimePeriod={"Start": start.isoformat(), "End": end.isoformat()}, Granularity="MONTHLY",
                         Metrics=["UnblendedCost"], Filter=NOT_CREDITS, GroupBy=[{"Type": "TAG", "Key": tag_key}]):
        for group in period["Groups"]:
            value = group["Keys"][0].split("$", 1)[-1] or "(untagged)"
            totals[value] = totals.get(value, 0.0) + _amount(group)
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)


def fetch_forecast(ce, start, end):
    resp = ce.get_cost_forecast(TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                                Metric="UNBLENDED_COST", Granularity="MONTHLY")
    return float(resp["Total"]["Amount"])


def collect(ce, today, cfg):
    """Calls Cost Explorer. Each piece fails independently so one missing piece never blocks the report."""
    start = core.month_start(today)
    notes, data = [], {"daily": [], "tags": [], "credits": None, "forecast": None}

    def attempt(label, fn):
        try:
            return fn()
        except ClientError as err:
            notes.append(f"{label} unavailable: {err.response['Error']['Code']} ({err.response['Error']['Message'][:120]})")
        except Exception as err:  # a malformed response should not stop the report either
            notes.append(f"{label} unavailable: {type(err).__name__}")
        return None

    data["daily"] = attempt("Daily cost data", lambda: fetch_daily(ce, min(start, today - timedelta(days=8)), today)) or []
    if start < today:
        data["credits"] = attempt("Credits", lambda: fetch_credits(ce, start, today))
        data["tags"] = attempt(f"Spend by {cfg['tag_key']} tag", lambda: fetch_by_tag(ce, cfg["tag_key"], start, today)) or []
    data["forecast"] = attempt("Forecast", lambda: fetch_forecast(ce, today, core.next_month_start(today)))
    if data["daily"] and not data["tags"]:
        notes.append(f"No spend grouped by the {cfg['tag_key']} tag. Activate it as a cost allocation tag in the Billing console (takes up to 24 hours).")
    return data, notes


def handler(event, context):
    cfg = load_config()
    now = datetime.now(timezone.utc)
    today = now.date()
    ce = boto3.client("ce")

    data, notes = collect(ce, today, cfg)
    summary = core.build_cost_summary(data["daily"], today, data["tags"], data["credits"], data["forecast"], notes)
    keys = awsio.store_report(boto3.client("s3"), cfg["report_bucket"], "cost", summary, core.render_cost_html(summary), now)
    subject = f"FinOps cost report: ${summary['month_to_date_usd']:.2f} month to date"
    notified = awsio.publish(boto3.client("sns"), cfg["topic_arn"], subject, core.render_cost_text(summary))
    return {"summary": summary, "report_keys": keys, "notified": notified}
