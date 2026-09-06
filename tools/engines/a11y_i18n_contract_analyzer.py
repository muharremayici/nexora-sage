from __future__ import annotations

import re
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.core.config import DYNAMIC_CONFIG, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.report_surface_limits import report_surface_limit
from tools.core.source_evidence import read_atlas_bound_source


INTERACTIVE_RE = re.compile(r"<(?:button|a|input|select|textarea)\b", re.IGNORECASE)
DIALOG_RE = re.compile(r"(?:<(?:Dialog|Modal|Popover|Sheet)\b|\brole\s*=\s*{?['\"]dialog['\"]}?|\baria-modal\s*=)", re.IGNORECASE)
ARIA_RE = re.compile(r"\baria-[a-zA-Z-]+\s*=")
FOCUS_RE = re.compile(r"\b(?:autoFocus|focus\(|tabIndex|onKeyDown|onEscapeKeyDown)\b")
FOCUS_TRAP_RE = re.compile(r"\b(?:FocusScope|trapFocus|initialFocus|finalFocus|restoreFocus|onOpenAutoFocus|onCloseAutoFocus)\b")
ARIA_RELATION_RE = re.compile(r"\b(?:aria-labelledby|aria-describedby|aria-controls|aria-owns)\s*=")
ID_ATTR_RE = re.compile(r"\bid\s*=")
RAW_TEXT_RE = re.compile(r">\s*[A-Z][A-Za-z ]{18,}\s*<")
I18N_FALLBACK_RE = re.compile(r"\b(?:t|i18n\.t)\s*\([^)]*(?:defaultValue|fallback)", re.DOTALL)
I18N_CALL_RE = re.compile(r"\b(?:t|i18n\.t)\s*\((?P<args>[^)]*)\)", re.DOTALL)
I18N_KEY_RE = re.compile(r"\b(?:t|i18n\.t)\s*\(\s*['\"]([^'\"]+)['\"]")
I18N_INTERPOLATION_RE = re.compile(r"\b(?:t|i18n\.t)\s*\([^)]*{[^}]+}", re.DOTALL)
I18N_PLURAL_RE = re.compile(r"\b(?:count|plural|one|other)\b", re.IGNORECASE)
IMG_TAG_RE = re.compile(r"<img\b([^>]*)>", re.IGNORECASE | re.DOTALL)
NEXT_IMAGE_RE = re.compile(r"(?:from\s+['\"]next/image['\"]|<Image\b)")
ALT_RE = re.compile(r"\balt\s*=")
IMAGE_LOADING_RE = re.compile(r"\b(?:loading|priority|fetchPriority|decoding|sizes)\s*=")
CONTROL_TAG_RE = re.compile(r"<(?:button|a)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?:button|a)>", re.IGNORECASE | re.DOTALL)
ACCESSIBLE_NAME_RE = re.compile(r"\b(?:aria-label|aria-labelledby|title)\s*=|sr-only", re.IGNORECASE)
ICON_ONLY_RE = re.compile(r"<[A-Z][A-Za-z0-9_]*(?:Icon)?\b|<svg\b", re.IGNORECASE)
VISIBLE_TEXT_RE = re.compile(r">\s*[^<>{}\s][^<>{}]*\s*<")
ANCHOR_TAG_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
BLANK_TARGET_RE = re.compile(r"\btarget\s*=\s*{?['\"]_blank['\"]}?", re.IGNORECASE)
SAFE_REL_RE = re.compile(r"\brel\s*=\s*{?['\"][^'\"]*(?:noopener|noreferrer)[^'\"]*['\"]}?", re.IGNORECASE)


def _dynamic_i18n_call_count_without_fallback(content: str) -> int:
    count = 0
    for match in I18N_CALL_RE.finditer(content):
        args = (match.group("args") or "").strip()
        if not args:
            continue
        if "defaultValue" in args or "fallback" in args:
            continue
        first_arg = args.split(",", 1)[0].strip()
        if first_arg.startswith(("'", '"')):
            continue
        if first_arg.startswith("`") and "${" not in first_arg:
            continue
        count += 1
    return count


def _project_root(project: str) -> Path:
    rel = (DYNAMIC_CONFIG.get("variations") or {}).get(project, "")
    return (ROOT / rel).resolve()


def _read(project: str, rel_path: str, atlas_entry: dict) -> str:
    return read_atlas_bound_source(
        component="a11y_i18n_contract_analyzer",
        project=project,
        project_root=_project_root(project),
        rel_path=rel_path,
        atlas_entry=atlas_entry,
        reason="JSX and i18n contract extraction",
    ) or ""


def _flatten_json_keys(value: Any, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            keys.add(next_prefix)
            keys.update(_flatten_json_keys(child, next_prefix))
    return keys


def _locale_completeness(project: str, project_data: dict) -> dict[str, Any]:
    root = _project_root(project)
    by_locale: dict[str, set[str]] = defaultdict(set)
    files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
    for rel_path, atlas_entry in files.items():
        rel = str(rel_path).replace("\\", "/")
        parts = Path(rel).parts
        if Path(rel).suffix.lower() != ".json" or "locales" not in parts:
            continue
        locale_index = parts.index("locales")
        if len(parts) <= locale_index + 2:
            continue
        locale = parts[locale_index + 1]
        content = _read(project, rel, atlas_entry)
        try:
            payload = json.loads(content) if content else {}
        except (TypeError, ValueError):
            payload = {}
        by_locale[locale].update(_flatten_json_keys(payload))
    if not by_locale:
        return {"locales": 0, "reference_locale": None, "completeness_ratio": 1.0, "missing_by_locale": {}}
    reference = max(by_locale, key=lambda key: len(by_locale[key]))
    reference_keys = by_locale[reference]
    missing = {
        locale: sorted(reference_keys - keys)[:80]
        for locale, keys in sorted(by_locale.items())
        if locale != reference and reference_keys - keys
    }
    total_expected = max(1, len(reference_keys) * max(1, len(by_locale) - 1))
    total_missing = sum(len(reference_keys - keys) for locale, keys in by_locale.items() if locale != reference)
    return {
        "locales": len(by_locale),
        "reference_locale": reference,
        "reference_key_count": len(reference_keys),
        "completeness_ratio": round(max(0.0, 1.0 - (total_missing / total_expected)), 3),
        "missing_by_locale": missing,
    }


def analyze_a11y_i18n_file(project: str, rel_path: str, content: str) -> dict[str, Any] | None:
    if "<" not in content:
        return None
    signals: list[str] = []
    risks: list[str] = []

    interactive = len(INTERACTIVE_RE.findall(content))
    dialogs = len(DIALOG_RE.findall(content))
    aria = len(ARIA_RE.findall(content))
    focus = len(FOCUS_RE.findall(content))
    focus_trap = len(FOCUS_TRAP_RE.findall(content))
    aria_relations = len(ARIA_RELATION_RE.findall(content))
    raw_text = len(RAW_TEXT_RE.findall(content))
    fallback = len(I18N_FALLBACK_RE.findall(content))
    i18n_keys = sorted(set(I18N_KEY_RE.findall(content)))
    i18n_interpolations = len(I18N_INTERPOLATION_RE.findall(content))
    i18n_plural_signals = len(I18N_PLURAL_RE.findall(content)) if i18n_interpolations else 0
    dynamic_i18n_without_fallback = _dynamic_i18n_call_count_without_fallback(content)
    img_tags = IMG_TAG_RE.findall(content)
    next_image = bool(NEXT_IMAGE_RE.search(content))
    images = len(img_tags) + (1 if next_image else 0)
    image_missing_alt = sum(1 for attrs in img_tags if not ALT_RE.search(attrs))
    raw_images_without_loading = sum(1 for attrs in img_tags if not IMAGE_LOADING_RE.search(attrs))
    unnamed_icon_controls = 0
    for match in CONTROL_TAG_RE.finditer(content):
        control = match.group(0)
        body = match.group("body") or ""
        if ACCESSIBLE_NAME_RE.search(control):
            continue
        if ICON_ONLY_RE.search(body) and not VISIBLE_TEXT_RE.search(body):
            unnamed_icon_controls += 1
    unsafe_blank_links = 0
    for match in ANCHOR_TAG_RE.finditer(content):
        attrs = match.group("attrs") or ""
        if BLANK_TARGET_RE.search(attrs) and not SAFE_REL_RE.search(attrs):
            unsafe_blank_links += 1

    if interactive:
        signals.append("interactive_controls")
    if dialogs:
        signals.append("dialog_or_overlay")
    if aria:
        signals.append("aria_contract")
    if focus:
        signals.append("focus_or_keyboard_contract")
    if focus_trap:
        signals.append("focus_trap_contract")
    if aria_relations:
        signals.append("aria_relation_contract")
    if fallback:
        signals.append("i18n_fallback_contract")
    if i18n_keys:
        signals.append("i18n_key_contract")
    if i18n_interpolations:
        signals.append("i18n_interpolation_contract")
    if dynamic_i18n_without_fallback:
        signals.append("i18n_dynamic_key_contract")
    if images:
        signals.append("image_media_contract")
    if next_image:
        signals.append("next_image_optimization")
    if unnamed_icon_controls:
        signals.append("icon_control_contract")
    if unsafe_blank_links:
        signals.append("external_link_security_contract")

    if dialogs and not focus:
        risks.append("dialog_without_visible_focus_contract")
    if dialogs and focus and not focus_trap:
        risks.append("dialog_without_explicit_focus_trap_contract")
    if aria_relations and not ID_ATTR_RE.search(content) and "useId(" not in content:
        risks.append("aria_relation_without_visible_id_contract")
    if interactive >= 3 and aria == 0:
        risks.append("interactive_surface_without_aria_contract")
    if raw_text >= 3 and fallback == 0 and "t(" not in content and "i18n.t" not in content:
        risks.append("raw_copy_without_i18n_contract")
    if i18n_interpolations and not i18n_plural_signals:
        risks.append("i18n_interpolation_without_plural_or_count_contract")
    if dynamic_i18n_without_fallback:
        risks.append("i18n_dynamic_key_without_fallback_contract")
    if image_missing_alt:
        risks.append("image_without_alt_contract")
    if raw_images_without_loading and not next_image:
        risks.append("raw_img_without_loading_or_optimization_contract")
    if unnamed_icon_controls:
        risks.append("icon_control_without_accessible_name")
    if unsafe_blank_links:
        risks.append("blank_target_link_without_noopener_contract")

    if not signals and not risks:
        return None
    tier = "high" if len(risks) >= 2 else ("medium" if risks else "low")
    return {
        "project": project,
        "file": rel_path.replace("\\", "/"),
        "signals": sorted(set(signals)),
        "risks": sorted(set(risks)),
        "risk_tier": tier,
        "counts": {
            "interactive": interactive,
            "dialogs": dialogs,
            "aria": aria,
            "focus": focus,
            "focus_trap": focus_trap,
            "aria_relations": aria_relations,
            "raw_text": raw_text,
            "i18n_fallback": fallback,
            "i18n_keys": len(i18n_keys),
            "i18n_interpolations": i18n_interpolations,
            "i18n_dynamic_without_fallback": dynamic_i18n_without_fallback,
            "images": images,
            "image_missing_alt": image_missing_alt,
            "raw_images_without_loading": raw_images_without_loading,
            "unnamed_icon_controls": unnamed_icon_controls,
            "unsafe_blank_links": unsafe_blank_links,
        },
        "a11y_contract": {
            "focus_journey": bool(focus),
            "focus_trap": bool(focus_trap),
            "aria_relation_graph": bool(aria_relations),
            "icon_accessible_names": unnamed_icon_controls == 0,
            "image_alt_contract": image_missing_alt == 0,
        },
        "i18n_contract": {
            "keys": i18n_keys[:80],
            "key_count": len(i18n_keys),
            "fallback_contract": bool(fallback),
            "interpolation_count": i18n_interpolations,
            "plural_or_count_contract": bool(i18n_plural_signals),
            "dynamic_key_without_fallback_count": dynamic_i18n_without_fallback,
        },
        "smoke_assertions": [
            assertion
            for assertion, enabled in (
                ("keyboard_focus_journey", bool(interactive or dialogs)),
                ("no_missing_accessible_names", bool(interactive or unnamed_icon_controls)),
                ("no_visible_raw_i18n_keys", bool(i18n_keys or raw_text)),
                ("aria_relations_resolve", bool(aria_relations)),
            )
            if enabled
        ],
    }


def run_a11y_i18n_contract_analyzer() -> dict[str, Any]:
    logger.info("Analyzing a11y/i18n UI contracts...")
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    rows: list[dict[str, Any]] = []
    if isinstance(atlas, dict):
        for project, pdata in atlas.items():
            files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            for rel_path in files.keys():
                if not str(rel_path).endswith((".tsx", ".jsx")):
                    continue
                result = analyze_a11y_i18n_file(project, rel_path, _read(project, rel_path, files.get(rel_path, {})))
                if result:
                    rows.append(result)

    risk_counts = Counter(risk for row in rows for risk in row.get("risks", []))
    signal_counts = Counter(signal for row in rows for signal in row.get("signals", []))
    by_project = defaultdict(int)
    locale_by_project = {}
    for row in rows:
        by_project[row["project"]] += 1
    if isinstance(atlas, dict):
        for project in atlas.keys():
            if project != "symbols":
                locale_by_project[project] = _locale_completeness(project, atlas.get(project, {}))

    primary_limit = report_surface_limit("a11y_i18n_contracts.primary_files")
    summary = {
        "files_analyzed": len(rows),
        "risk_counts": dict(risk_counts),
        "signal_counts": dict(signal_counts),
        "locale_completeness": locale_by_project,
        "high_risk": sum(1 for row in rows if row.get("risk_tier") == "high"),
        "medium_risk": sum(1 for row in rows if row.get("risk_tier") == "medium"),
        "by_project": dict(sorted(by_project.items())),
        "primary_report_file_limit": primary_limit,
        "truncated_in_primary_report": max(0, len(rows) - primary_limit),
        "full_artifact": "a11y_i18n_contracts_full.json",
    }
    payload = {
        "meta": {"kind": "a11y_i18n_contracts", "version": "v1"},
        "summary": summary,
        "files": rows,
    }
    full_payload = {
        **payload,
        "meta": {"kind": "a11y_i18n_contracts_full", "version": "v1", "source": "a11y_i18n_contracts"},
    }
    save_json_atomic(RAW_DIR / "a11y_i18n_contracts.json", payload)
    save_json_atomic(RAW_DIR / "a11y_i18n_contracts_full.json", full_payload)

    lines = [
        "# A11y + i18n Contract Analysis",
        "",
        f"- Files analyzed: `{payload['summary']['files_analyzed']}`",
        f"- High risk: `{payload['summary']['high_risk']}`",
        f"- Medium risk: `{payload['summary']['medium_risk']}`",
        "",
        "## Risk Counts",
        "",
    ]
    if risk_counts:
        for risk, count in risk_counts.most_common():
            lines.append(f"- `{risk}`: `{count}`")
    else:
        lines.append("- No a11y/i18n risks detected.")
    lines.extend(["", "## Files", "", "| Project | File | Tier | Signals | Risks |", "|---|---|---|---|---|"])
    for row in rows[:primary_limit]:
        lines.append(
            f"| `{row['project']}` | `{row['file']}` | `{row['risk_tier']}` | "
            f"`{', '.join(row['signals'])}` | `{', '.join(row['risks']) or '-'}` |"
        )
    save_text_atomic(REPORTS_DIR / "a11y_i18n_contracts.md", "\n".join(lines) + "\n")
    return payload


if __name__ == "__main__":
    run_a11y_i18n_contract_analyzer()
