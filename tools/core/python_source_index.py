from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping


def _syntax_error_payload(error: SyntaxError | None) -> dict[str, object] | None:
    if error is None:
        return None
    return {
        "message": str(error.msg),
        "line": int(error.lineno or 0),
        "offset": int(error.offset or 0),
    }


@dataclass(frozen=True)
class PythonSourceRecord:
    path: Path
    relative_path: str
    size_bytes: int
    sha256: str
    strict_text: str | None
    replacement_text: str
    strict_tree: ast.Module | None
    replacement_tree: ast.Module | None
    strict_syntax_error: SyntaxError | None
    replacement_syntax_error: SyntaxError | None
    utf8_decode_error: str | None

    def manifest_row(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "utf8_status": "invalid" if self.utf8_decode_error else "valid",
            "strict_syntax_error": _syntax_error_payload(self.strict_syntax_error),
            "replacement_syntax_error": _syntax_error_payload(self.replacement_syntax_error),
        }


@dataclass(frozen=True)
class PythonSourceIndex:
    root: Path
    records: tuple[PythonSourceRecord, ...]
    content_sha256: str
    read_count: int
    parse_count: int
    _records_by_path: Mapping[Path, PythonSourceRecord]

    @classmethod
    def build(cls, root: Path, paths: Iterable[Path]) -> "PythonSourceIndex":
        resolved_root = root.resolve()
        resolved_paths = [Path(path).resolve() for path in paths]
        if len(resolved_paths) != len(set(resolved_paths)):
            raise ValueError("Python source index paths must be unique")

        records: list[PythonSourceRecord] = []
        parse_count = 0
        for path in sorted(resolved_paths, key=lambda value: value.as_posix().casefold()):
            try:
                relative_path = path.relative_to(resolved_root).as_posix()
            except ValueError as exc:
                raise ValueError(f"Python source index path escapes root: {path}") from exc
            if not path.is_file():
                raise FileNotFoundError(f"Python source index path is not a file: {path}")

            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            try:
                strict_text = raw.decode("utf-8")
                utf8_decode_error = None
            except UnicodeDecodeError as exc:
                strict_text = None
                utf8_decode_error = f"{exc.start}:{exc.end}:{exc.reason}"

            strict_tree: ast.Module | None = None
            strict_syntax_error: SyntaxError | None = None
            if strict_text is not None:
                parse_count += 1
                try:
                    strict_tree = ast.parse(strict_text, filename=relative_path)
                except SyntaxError as exc:
                    strict_syntax_error = exc
                replacement_text = strict_text
                replacement_tree = strict_tree
                replacement_syntax_error = strict_syntax_error
            else:
                replacement_text = raw.decode("utf-8", errors="replace")
                parse_count += 1
                try:
                    replacement_tree = ast.parse(replacement_text, filename=relative_path)
                    replacement_syntax_error = None
                except SyntaxError as exc:
                    replacement_tree = None
                    replacement_syntax_error = exc

            records.append(
                PythonSourceRecord(
                    path=path,
                    relative_path=relative_path,
                    size_bytes=len(raw),
                    sha256=digest,
                    strict_text=strict_text,
                    replacement_text=replacement_text,
                    strict_tree=strict_tree,
                    replacement_tree=replacement_tree,
                    strict_syntax_error=strict_syntax_error,
                    replacement_syntax_error=replacement_syntax_error,
                    utf8_decode_error=utf8_decode_error,
                )
            )

        manifest_rows = [record.manifest_row() for record in records]
        content_sha256 = hashlib.sha256(
            json.dumps(
                manifest_rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        records_by_path = MappingProxyType({record.path: record for record in records})
        return cls(
            root=resolved_root,
            records=tuple(records),
            content_sha256=content_sha256,
            read_count=len(records),
            parse_count=parse_count,
            _records_by_path=records_by_path,
        )

    def record(self, path: Path) -> PythonSourceRecord:
        resolved = Path(path).resolve()
        try:
            return self._records_by_path[resolved]
        except KeyError as exc:
            raise KeyError(f"Python source path is not indexed: {resolved}") from exc

    def manifest(self) -> dict[str, object]:
        return {
            "content_sha256": self.content_sha256,
            "source_files": len(self.records),
            "read_count": self.read_count,
            "parse_count": self.parse_count,
            "files": [record.manifest_row() for record in self.records],
        }
