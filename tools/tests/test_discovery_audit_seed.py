from tools.orchestrators.discovery import build_audit_seed


def test_audit_seed_leaves_object_only_report_sections_to_audit_policy(monkeypatch):
    monkeypatch.setattr(
        "tools.orchestrators.discovery.ARCH_PROFILES",
        {"FSD": {"display_name": "Feature Sliced", "enabled_rules": ["boundaries"]}},
    )

    seed = build_audit_seed(["FSD"])

    assert seed["rules"] == {"boundaries": {"enabled": True, "source": "architecture_profiles.json"}}
    assert seed["report_sections"] == []
