"""Private one-declaration retention at the actual Atlas auxiliary read owner."""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import stat

from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs, auxiliary_context_identity
from tools.core.artifact_validator import ensure_against_schema
from tools.core.json_io import load_json_object_strict
from tools.core.owned_text_bridge import canonical

ROOT = Path(__file__).resolve().parents[2]


class OwnedDeclarationError(ValueError):
    """Safe private refusal code only, without source/OS exception messages."""


@dataclass(frozen=True)
class OwnedDeclarationInput:
    raw: bytes = field(repr=False)
    inventory_json: str = field(repr=False)
    context_sha256: str
    policy_sha256: str

    def wire_source(self):
        import base64
        inventory = json.loads(self.inventory_json)
        row = inventory["files"]["types.d.ts"]
        if hashlib.sha256(self.raw).hexdigest() != row["raw_sha256"]:
            raise OwnedDeclarationError("declaration_input_changed")
        return {"bytes": len(self.raw), "raw_sha256": row["raw_sha256"],
            "host_text_sha256": row["host_text_sha256"],
            "utf8Base64": base64.b64encode(self.raw).decode("ascii")}


def capture_owned_declaration(path: Path, *, root: Path) -> OwnedDeclarationInput:
    settings = load_json_object_strict(ROOT / "config/analysis_snapshot_lineage_contract.json")[
        "artifacts"]["ts_diagnostics"]["private_owned_declaration_handoff"]
    ensure_against_schema(ROOT / settings["schema"], "owned_declaration_handoff", settings)
    try:
        root, path = Path(root).absolute(), Path(path).absolute()
        if (str(root).startswith(("\\\\", "//")) or root.resolve(strict=True) != root
                or path != root / settings["auxiliary_atlas_path"] or path.resolve(strict=True) != path):
            raise OwnedDeclarationError("declaration_path_refused")
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise OwnedDeclarationError("declaration_path_refused")
        retained = []
        def observed(relative, raw, row):
            if (relative != settings["auxiliary_atlas_path"] or row.get("canonical_file_path") is not True
                    or len(raw) > settings["max_auxiliary_bytes"]):
                raise OwnedDeclarationError("declaration_capture_refused")
            retained.append(raw)
        inventory = capture_auxiliary_inputs(root, [(str(path), path.name, settings["auxiliary_atlas_path"])],
            _private_owned_read=observed, _private_max_file_bytes=settings["max_auxiliary_bytes"])
        identity = auxiliary_context_identity(inventory)
        if (not identity or len(retained) != 1 or inventory["selected_files"] != 1
                or inventory["omitted_files"] or set(inventory["files"]) != {settings["auxiliary_atlas_path"]}):
            raise OwnedDeclarationError("declaration_capture_refused")
    except OwnedDeclarationError:
        raise
    except (OSError, ValueError, UnicodeError, KeyError):
        raise OwnedDeclarationError("declaration_capture_unavailable") from None
    return OwnedDeclarationInput(retained[0], canonical(inventory), identity,
        hashlib.sha256(canonical(settings).encode("utf-8")).hexdigest())
