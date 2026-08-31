from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.analysis_snapshot_lineage import evaluate_snapshot_bound_inputs
from tools.core.config import CONFIG_DIR


CONTRACT_PATH = CONFIG_DIR / "surgical_packet_input_contract.json"


def evaluate_surgical_packet_inputs(
    *,
    raw_dir: Path,
    expected_snapshot_id: str,
    payloads: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    return evaluate_snapshot_bound_inputs(
        contract_path=CONTRACT_PATH,
        raw_dir=raw_dir,
        expected_snapshot_id=expected_snapshot_id,
        payloads=payloads,
    )
