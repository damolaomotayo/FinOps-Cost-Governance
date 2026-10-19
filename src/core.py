"""Pure business logic shared by the Lambda handlers.

Nothing in here talks to AWS, so everything is easy to test and to explain.
"""
import html
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

# --- approximate prices (USD) -----------------------------------------------
# Real prices vary by region and change over time. These exist to ESTIMATE waste
# so findings can be ranked by money; they are not a billing source.
EBS_GB_MONTH = {"gp3": 0.08, "gp2": 0.10, "io1": 0.125, "io2": 0.125, "st1": 0.045, "sc1": 0.015, "standard": 0.05}
SNAPSHOT_GB_MONTH = 0.05
PUBLIC_IPV4_HOURLY = 0.005  # AWS charges for every public IPv4 address, attached or not
HOURS_PER_MONTH = 730
EC2_HOURLY = {
    "t3.nano": 0.0052, "t3.micro": 0.0104, "t3.small": 0.0208, "t3.medium": 0.0416, "t3.large": 0.0832,
    "t3.xlarge": 0.1664, "t4g.nano": 0.0042, "t4g.micro": 0.0084, "t4g.small": 0.0168, "t4g.medium": 0.0336,
    "t4g.large": 0.0672, "m5.large": 0.096, "m5.xlarge": 0.192, "m6i.large": 0.096, "m6i.xlarge": 0.192,
    "c5.large": 0.085, "c5.xlarge": 0.17, "r5.large": 0.126,
}

IGNORE_TAG = "finops:ignore"  # put finops:ignore=true on a resource to exempt it from findings


@dataclass
class Finding:
    kind: str
    resource_id: str
    detail: str
    monthly_cost_usd: float
    recommendation: str
    region: str = ""


# --- small helpers -------------------------------------------------------------

def tags_to_dict(tag_list):
    return {t["Key"]: t.get("Value", "") for t in (tag_list or [])}


def is_ignored(tags):
    return str(tags.get(IGNORE_TAG, "")).lower() == "true"


def missing_tags(tags, required):
    return [key for key in required if not str(tags.get(key, "")).strip()]


def volume_monthly_cost(size_gb, volume_type):
    return round(size_gb * EBS_GB_MONTH.get(volume_type, 0.10), 2)


def snapshot_monthly_cost(size_gb):
    return round(size_gb * SNAPSHOT_GB_MONTH, 2)  # upper bound: real snapshots are incremental


def public_ip_monthly_cost():
    return round(PUBLIC_IPV4_HOURLY * HOURS_PER_MONTH, 2)


def instance_monthly_cost(instance_type):
    return round(EC2_HOURLY.get(instance_type, 0.0) * HOURS_PER_MONTH, 2)


_STOP_RE = re.compile(r"\((\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) GMT\)")


def parse_stop_time(state_transition_reason):
    """EC2 writes e.g. 'User initiated (2026-10-01 10:00:00 GMT)'. Returns an aware datetime or None."""
    match = _STOP_RE.search(state_transition_reason or "")
    if not match:
        return None
    return datetime.strptime(" ".join(match.groups()), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


# --- scan report ---------------------------------------------------------------

def build_scan_report(findings, non_compliant, checked, now, region):
    ordered = sorted(findings, key=lambda f: f.monthly_cost_usd, reverse=True)
    by_kind = {}
    for f in ordered:
        entry = by_kind.setdefault(f.kind, {"count": 0, "monthly_cost_usd": 0.0})
        entry["count"] += 1
        entry["monthly_cost_usd"] = round(entry["monthly_cost_usd"] + f.monthly_cost_usd, 2)
    total_checked = sum(checked.values())
    compliance = 100.0 if total_checked == 0 else round(100.0 * (total_checked - len(non_compliant)) / total_checked, 1)
    return {
        "summary": {
            "generated_at": now.isoformat(),
            "region": region,
            "findings_count": len(ordered),
            "estimated_monthly_waste_usd": round(sum(f.monthly_cost_usd for f in ordered), 2),
            "by_kind": by_kind,
            "resources_checked": checked,
            "non_compliant_count": len(non_compliant),
            "compliance_pct": compliance,
        },
        "findings": [asdict(f) for f in ordered],
        "non_compliant": non_compliant,
    }


def render_scan_text(report):
    s = report["summary"]
    lines = [
        f"FinOps scan for {s['region']} at {s['generated_at']}",
        f"Estimated monthly waste: ${s['estimated_monthly_waste_usd']:.2f} across {s['findings_count']} findings",
        f"Tag compliance: {s['compliance_pct']}% ({s['non_compliant_count']} non-compliant of {sum(s['resources_checked'].values())} checked)",
        "(Cost figures are estimates from approximate list prices.)",
        "",
        "Waste by type:",
    ]
    for kind, v in s["by_kind"].items():
        lines.append(f"  {kind}: {v['count']} resources, ~${v['monthly_cost_usd']:.2f}/month")
    lines.append("")
    lines.append("Top findings:")
    for f in report["findings"][:10]:
        lines.append(f"  ${f['monthly_cost_usd']:.2f}/mo  {f['kind']}  {f['resource_id']}  {f['detail']}")
    if report["non_compliant"]:
        lines.append("")
        lines.append("Missing required tags (first 10):")
        for n in report["non_compliant"][:10]:
            lines.append(f"  {n['type']} {n['id']}  missing: {', '.join(n['missing'])}")
    return "\n".join(lines)


def _table(headers, rows):
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


_STYLE = (
    "<style>body{font:15px system-ui,sans-serif;margin:2rem;max-width:1000px}table{border-collapse:collapse;width:100%;margin:1rem 0}"
    "th,td{border:1px solid #ccc;padding:6px 10px;text-align:left}th{background:#f3f3f3}.big{font-size:1.6rem;font-weight:600}</style>"
)


def render_scan_html(report):
    s = report["summary"]
    findings = _table(
        ["Monthly cost (USD)", "Type", "Resource", "Detail", "Recommendation"],
        [[f"{f['monthly_cost_usd']:.2f}", f["kind"], f["resource_id"], f["detail"], f["recommendation"]] for f in report["findings"]],
    )
    tags = _table(
        ["Type", "Resource", "Name", "Missing tags"],
        [[n["type"], n["id"], n.get("name", ""), ", ".join(n["missing"])] for n in report["non_compliant"]],
    )
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>FinOps scan</title>{_STYLE}</head><body>"
        f"<h1>FinOps scan: {html.escape(s['region'])}</h1><p>{html.escape(s['generated_at'])}</p>"
        f"<p class='big'>~${s['estimated_monthly_waste_usd']:.2f} / month estimated waste</p>"
        f"<p>Tag compliance: {s['compliance_pct']}% ({s['non_compliant_count']} non-compliant)</p>"
        f"<h2>Waste findings</h2>{findings}<h2>Resources missing required tags</h2>{tags}"
        "<p><small>Estimates use approximate list prices.</small></p></body></html>"
    )


# --- cost report ---------------------------------------------------------------

def month_start(day):
    return day.replace(day=1)


def next_month_start(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def detect_spikes(rows, today, ratio=1.5, min_usd=1.0):
    """Services whose cost yesterday is well above their average over the 7 days before.

    rows: [{"date": "YYYY-MM-DD", "service": str, "amount": float}]
    """
    yesterday = today - timedelta(days=1)
    baseline_days = {(yesterday - timedelta(days=i)).isoformat() for i in range(1, 8)}
    y_by_service, base_by_service = {}, {}
    for r in rows:
        if r["date"] == yesterday.isoformat():
            y_by_service[r["service"]] = y_by_service.get(r["service"], 0.0) + r["amount"]
        elif r["date"] in baseline_days:
            base_by_service[r["service"]] = base_by_service.get(r["service"], 0.0) + r["amount"]
    spikes = []
    for service, y in y_by_service.items():
        avg = base_by_service.get(service, 0.0) / 7
        if y >= min_usd and (avg == 0 or y > avg * ratio):
            spikes.append({"service": service, "yesterday_usd": round(y, 2), "baseline_daily_avg_usd": round(avg, 2),
                           "note": "new spend" if avg == 0 else f"{y / avg:.1f}x the 7-day average"})
    return sorted(spikes, key=lambda s: s["yesterday_usd"], reverse=True)


def build_cost_summary(daily_rows, today, tag_rows=None, credits_usd=None, forecast_usd=None, notes=None):
    """Turns raw Cost Explorer rows into the numbers a human wants to read."""
    yesterday = today - timedelta(days=1)
    start = month_start(today)
    mtd_by_service, yesterday_total, week_total = {}, 0.0, 0.0
    week_days = {(yesterday - timedelta(days=i)).isoformat() for i in range(1, 8)}
    for r in daily_rows or []:
        day = date.fromisoformat(r["date"])
        if start <= day < today:
            mtd_by_service[r["service"]] = mtd_by_service.get(r["service"], 0.0) + r["amount"]
        if r["date"] == yesterday.isoformat():
            yesterday_total += r["amount"]
        elif r["date"] in week_days:
            week_total += r["amount"]
    mtd_total = sum(mtd_by_service.values())
    top = sorted(mtd_by_service.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return {
        "generated_for": today.isoformat(),
        "month_to_date_usd": round(mtd_total, 2),
        "yesterday_usd": round(yesterday_total, 2),
        "previous_7_day_daily_avg_usd": round(week_total / 7, 2),
        "forecast_month_end_usd": None if forecast_usd is None else round(forecast_usd, 2),
        "credits_applied_month_to_date_usd": None if credits_usd is None else round(abs(credits_usd), 2),
        "top_services_month_to_date": [{"service": s, "usd": round(v, 2)} for s, v in top],
        "spend_by_project_tag": [{"project": p, "usd": round(v, 2)} for p, v in (tag_rows or [])],
        "spikes": detect_spikes(daily_rows or [], today),
        "notes": notes or [],
    }


def render_cost_text(summary):
    forecast = "n/a" if summary["forecast_month_end_usd"] is None else f"${summary['forecast_month_end_usd']:.2f}"
    credits = "n/a" if summary["credits_applied_month_to_date_usd"] is None else f"${summary['credits_applied_month_to_date_usd']:.2f}"
    lines = [
        f"FinOps cost report for {summary['generated_for']}",
        f"Month to date (gross, before credits): ${summary['month_to_date_usd']:.2f}",
        f"Credits applied this month: {credits}",
        f"Forecast for the month: {forecast}",
        f"Yesterday: ${summary['yesterday_usd']:.2f}  (previous 7-day daily average: ${summary['previous_7_day_daily_avg_usd']:.2f})",
        "(Cost Explorer data can lag by up to a day, so yesterday may be incomplete.)",
        "",
        "Top services this month:",
    ]
    lines += [f"  ${s['usd']:.2f}  {s['service']}" for s in summary["top_services_month_to_date"]] or ["  (no data)"]
    lines += ["", "Spend by Project tag:"]
    lines += [f"  ${p['usd']:.2f}  {p['project']}" for p in summary["spend_by_project_tag"]] or ["  (no data; activate the Project cost allocation tag)"]
    lines += ["", "Spikes vs 7-day average:"]
    lines += [f"  {s['service']}: ${s['yesterday_usd']:.2f} yesterday ({s['note']})" for s in summary["spikes"]] or ["  none"]
    if summary["notes"]:
        lines += ["", "Notes:"] + [f"  {n}" for n in summary["notes"]]
    return "\n".join(lines)


def render_cost_html(summary):
    top = _table(["Service", "Month to date (USD)"], [[s["service"], f"{s['usd']:.2f}"] for s in summary["top_services_month_to_date"]])
    tags = _table(["Project tag", "Month to date (USD)"], [[p["project"], f"{p['usd']:.2f}"] for p in summary["spend_by_project_tag"]])
    spikes = _table(["Service", "Yesterday (USD)", "Note"], [[s["service"], f"{s['yesterday_usd']:.2f}", s["note"]] for s in summary["spikes"]])
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in summary["notes"])
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>FinOps cost report</title>{_STYLE}</head><body>"
        f"<h1>FinOps cost report</h1><p>{html.escape(summary['generated_for'])}</p>"
        f"<p class='big'>${summary['month_to_date_usd']:.2f} month to date (gross, before credits)</p>"
        f"<h2>Top services</h2>{top}<h2>Spend by Project tag</h2>{tags}<h2>Spikes</h2>{spikes}"
        f"<ul>{notes}</ul></body></html>"
    )
