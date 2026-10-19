from datetime import date, datetime, timezone

import core


def test_missing_tags_treats_blank_as_missing():
    assert core.missing_tags({"Owner": "a", "Project": " "}, ["Owner", "Project", "Environment"]) == ["Project", "Environment"]


def test_ignore_tag():
    assert core.is_ignored({"finops:ignore": "TRUE"})
    assert not core.is_ignored({"finops:ignore": "no"})


def test_costs():
    assert core.volume_monthly_cost(100, "gp3") == 8.0
    assert core.public_ip_monthly_cost() == 3.65
    assert core.instance_monthly_cost("t3.micro") == 7.59
    assert core.instance_monthly_cost("unknown.type") == 0.0


def test_parse_stop_time():
    got = core.parse_stop_time("User initiated (2026-10-01 10:00:00 GMT)")
    assert got == datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    assert core.parse_stop_time("") is None
    assert core.parse_stop_time(None) is None


def test_month_helpers():
    assert core.month_start(date(2026, 10, 19)) == date(2026, 10, 1)
    assert core.next_month_start(date(2026, 12, 31)) == date(2027, 1, 1)
    assert core.next_month_start(date(2026, 2, 10)) == date(2026, 3, 1)


def test_spike_detection():
    today = date(2026, 10, 19)
    rows = [{"date": f"2026-10-{d:02d}", "service": "EC2", "amount": 2.0} for d in range(11, 18)]
    rows.append({"date": "2026-10-18", "service": "EC2", "amount": 10.0})
    rows.append({"date": "2026-10-18", "service": "S3", "amount": 0.2})  # below min_usd
    rows.append({"date": "2026-10-18", "service": "NAT", "amount": 3.0})  # brand new spend
    spikes = {s["service"]: s for s in core.detect_spikes(rows, today)}
    assert set(spikes) == {"EC2", "NAT"}
    assert spikes["EC2"]["note"] == "5.0x the 7-day average"
    assert spikes["NAT"]["note"] == "new spend"


def test_no_spike_when_flat():
    today = date(2026, 10, 19)
    rows = [{"date": f"2026-10-{d:02d}", "service": "EC2", "amount": 2.0} for d in range(11, 19)]
    assert core.detect_spikes(rows, today) == []


def test_scan_report_ranks_and_scores():
    findings = [core.Finding("a", "1", "d", 1.0, "r"), core.Finding("b", "2", "d", 5.0, "r")]
    nc = [{"type": "ec2_instance", "id": "i-1", "name": "", "missing": ["Owner"]}]
    report = core.build_scan_report(findings, nc, {"ec2_instance": 4}, datetime(2026, 10, 19, tzinfo=timezone.utc), "us-east-1")
    assert [f["resource_id"] for f in report["findings"]] == ["2", "1"]
    assert report["summary"]["estimated_monthly_waste_usd"] == 6.0
    assert report["summary"]["compliance_pct"] == 75.0
    assert "FinOps scan" in core.render_scan_text(report)
    assert "<table>" in core.render_scan_html(report)


def test_scan_report_empty_is_fully_compliant():
    report = core.build_scan_report([], [], {"ec2_instance": 0}, datetime(2026, 10, 19, tzinfo=timezone.utc), "r")
    assert report["summary"]["compliance_pct"] == 100.0


def test_cost_summary_and_render():
    today = date(2026, 10, 19)
    rows = [{"date": "2026-10-01", "service": "EC2", "amount": 5.0}, {"date": "2026-10-18", "service": "EC2", "amount": 1.0}]
    s = core.build_cost_summary(rows, today, [("web", 3.0)], credits_usd=-2.0, forecast_usd=30.0)
    assert s["month_to_date_usd"] == 6.0
    assert s["credits_applied_month_to_date_usd"] == 2.0
    assert s["yesterday_usd"] == 1.0
    assert "$6.00" in core.render_cost_text(s)
    assert "web" in core.render_cost_html(s)


def test_cost_summary_without_data():
    s = core.build_cost_summary([], date(2026, 10, 19), notes=["no CE"])
    assert s["month_to_date_usd"] == 0.0 and s["forecast_month_end_usd"] is None
    assert "no CE" in core.render_cost_text(s)
