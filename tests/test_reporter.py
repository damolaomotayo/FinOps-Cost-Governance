from datetime import date

from botocore.exceptions import ClientError

import reporter

TODAY = date(2026, 10, 19)
CFG = {"tag_key": "Project"}


def period(day, groups):
    return {"TimePeriod": {"Start": day}, "Groups": [
        {"Keys": [k], "Metrics": {"UnblendedCost": {"Amount": str(v)}}} for k, v in groups]}


class FakeCE:
    def __init__(self, fail=()):
        self.fail, self.calls = set(fail), []

    def _check(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise ClientError({"Error": {"Code": "DataUnavailableException", "Message": "nope"}}, name)

    def get_cost_and_usage(self, **kw):
        group = kw["GroupBy"][0]
        if group["Key"] == "RECORD_TYPE":
            self._check("credits")
            return {"ResultsByTime": [period("2026-10-01", [("Usage", 20.0), ("Credit", -5.0)])]}
        if group["Type"] == "TAG":
            self._check("tags")
            return {"ResultsByTime": [period("2026-10-01", [("Project$web", 12.0), ("Project$", 3.0)])]}
        self._check("daily")
        return {"ResultsByTime": [period("2026-10-01", [("EC2", 4.0)]), period("2026-10-18", [("EC2", 9.0)])]}

    def get_cost_forecast(self, **kw):
        self._check("forecast")
        return {"Total": {"Amount": "42.5"}}


def test_collect_all_parts():
    data, notes = reporter.collect(FakeCE(), TODAY, CFG)
    assert len(data["daily"]) == 2 and data["credits"] == -5.0 and data["forecast"] == 42.5
    assert dict(data["tags"]) == {"web": 12.0, "(untagged)": 3.0}
    assert notes == []


def test_pieces_fail_independently():
    data, notes = reporter.collect(FakeCE(fail={"tags", "forecast"}), TODAY, CFG)
    assert data["daily"] and data["credits"] == -5.0
    assert data["forecast"] is None and data["tags"] == []
    assert any("Forecast unavailable" in n for n in notes) and any("tag unavailable" in n for n in notes)


def test_all_failing_still_returns():
    data, notes = reporter.collect(FakeCE(fail={"daily", "credits", "tags", "forecast"}), TODAY, CFG)
    assert data["daily"] == [] and len(notes) == 4


def test_first_of_month_skips_month_queries():
    ce = FakeCE()
    reporter.collect(ce, date(2026, 10, 1), CFG)
    assert "credits" not in ce.calls and "tags" not in ce.calls


def test_untagged_hint_when_no_tag_data():
    class NoTags(FakeCE):
        def get_cost_and_usage(self, **kw):
            if kw["GroupBy"][0]["Type"] == "TAG":
                return {"ResultsByTime": []}
            return super().get_cost_and_usage(**kw)
    _, notes = reporter.collect(NoTags(), TODAY, CFG)
    assert any("cost allocation tag" in n for n in notes)
