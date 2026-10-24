import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_prometheus_loads_b2c_rules_and_compose_mounts_them():
    prometheus = yaml.safe_load((ROOT / "docker/prometheus/prometheus.yml").read_text())
    compose = yaml.safe_load((ROOT / "docker/compose.yml").read_text())
    service = compose["services"]["prometheus"]
    mounts = service["volumes"]
    assert "/etc/prometheus/alerts.yml" in prometheus["rule_files"]
    assert any(
        isinstance(mount, str)
        and mount.startswith("./prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro")
        for mount in mounts
    )


def test_b2c_alert_rules_are_scoped_and_have_documented_budget():
    rules = yaml.safe_load((ROOT / "docker/prometheus/alerts.yml").read_text())
    alerts = [rule["alert"] for group in rules["groups"] for rule in group["rules"]]
    assert alerts == ["B2CScrapeTargetDown", "B2CContainerMemoryBudgetHigh"]
    memory = next(
        rule for group in rules["groups"] for rule in group["rules"]
        if rule["alert"] == "B2CContainerMemoryBudgetHigh"
    )
    assert "container_label_com_docker_compose_project=\"b2c\"" in memory["expr"]
    assert "83751862272" in memory["expr"]


def test_grafana_dashboard_is_provisioned_read_only_and_uses_b2c_datasource():
    compose = yaml.safe_load((ROOT / "docker/compose.yml").read_text())
    dashboard_provider = yaml.safe_load(
        (ROOT / "docker/grafana/provisioning/dashboards/b2c.yml").read_text()
    )
    datasource = yaml.safe_load(
        (ROOT / "docker/grafana/provisioning/datasources/prometheus.yml").read_text()
    )
    dashboard = json.loads((ROOT / "docker/grafana/dashboards/b2c-operations.json").read_text())
    grafana_mounts = compose["services"]["grafana"]["volumes"]
    assert any(
        isinstance(mount, str)
        and mount.startswith("./grafana/dashboards:/etc/grafana/dashboards:ro")
        for mount in grafana_mounts
    )
    assert dashboard_provider["providers"][0]["options"]["path"] == "/etc/grafana/dashboards"
    datasource_uid = datasource["datasources"][0]["uid"]
    assert datasource_uid == "PBFA97CFB590B2093"
    assert dashboard["uid"] == "b2c-ops"
    assert dashboard["panels"]
    assert all(
        panel["datasource"]["uid"] == datasource_uid
        for panel in dashboard["panels"]
    )
