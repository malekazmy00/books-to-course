"""R0 containment (audit 08 H3, plan R0 item 10): minimal operational alerts.

The rules may only use metric names that this stack's Prometheus actually holds. They were read
from the live Prometheus (``/api/v1/label/__name__/values``) on 2026-10-25: the Temporal server
(job ``temporal``) exposes ``approximate_backlog_count`` and ``poll_success``/``poll_timeouts``
per task queue, and ``activity_success``/``activity_heartbeat_count``. ``temporal_num_pollers``
exists only for the server's own Go system workers (``client_name="temporal_go"``); the b2c
Python workers export no SDK metrics, so it cannot see b2c queues and is not used.

Behaviour of the rules is checked by ``docker/prometheus/alerts.test.yml`` (``promtool test rules``).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
VERIFIED_METRICS = {
    "up", "container_memory_working_set_bytes",
    "approximate_backlog_count", "poll_success", "poll_timeouts",
    "activity_success", "activity_heartbeat_count",
}
PROMQL_WORDS = {"sum", "by", "rate", "increase", "unless", "on", "and", "or", "max", "min", "count",
                "namespace", "taskqueue", "task_type", "job", "instance", "container_label_com_docker_compose_project",
                "__name__", "m", "h", "s", "b2c", "default", "bool"}


def _rules() -> dict[str, dict]:
    data = yaml.safe_load((ROOT / "docker/prometheus/alerts.yml").read_text())
    return {rule["alert"]: rule for group in data["groups"] for rule in group["rules"]}


def _metric_names(expr: str) -> set[str]:
    without_strings = re.sub(r'"[^"]*"', '""', expr)
    without_ranges = re.sub(r"\[[^\]]*\]", "", without_strings)
    names = set(re.findall(r"[A-Za-z_:][A-Za-z0-9_:]*", without_ranges)) - PROMQL_WORDS
    names |= set(re.findall(r'__name__=~"([^"]+)"', expr)[0].split("|")) if "__name__=~" in expr else set()
    return names


def test_backlog_without_pollers_alert_fires_after_five_minutes():
    rule = _rules()["B2CTaskQueueBacklogWithoutPollers"]
    assert rule["for"] == "5m"
    assert "approximate_backlog_count" in rule["expr"]
    assert "poll_success" in rule["expr"] and "poll_timeouts" in rule["expr"]
    assert rule["labels"]["project"] == "b2c"


def test_no_activity_progress_alert_covers_twenty_four_hours():
    rule = _rules()["B2CNoActivityProgress24h"]
    assert "[24h]" in rule["expr"]
    assert "activity_success" in rule["expr"] and "activity_heartbeat_count" in rule["expr"]
    assert "proxy" in rule["annotations"]["description"].lower()


def test_rules_use_only_metrics_present_in_this_stack():
    for name, rule in _rules().items():
        unknown = _metric_names(rule["expr"]) - VERIFIED_METRICS
        assert not unknown, (name, unknown)


def test_rules_have_promtool_unit_tests():
    tests = yaml.safe_load((ROOT / "docker/prometheus/alerts.test.yml").read_text())
    assert tests["rule_files"] == ["alerts.yml"]
    tested = {alert["alertname"] for case in tests["tests"] for alert in case["alert_rule_test"]}
    assert {"B2CTaskQueueBacklogWithoutPollers", "B2CNoActivityProgress24h"} <= tested
