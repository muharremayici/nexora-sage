"""Read-only XML 1.0 output oracle for an explicit target-produced UTF-8 file.

Usage: python -m tools.validate_xml_format_output --xml-file output.xml
       --expectations-file expected.json

Expectations JSON contains root_tag, texts and optional attributes. At least
one value expectation is required. Text rows have tag, zero-based occurrence
and expected_text; attribute rows have tag, occurrence, attribute and
expected_value. Reports never echo XML or values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.core.xml_format_oracle import MAX_XML_CHARACTERS, evaluate_xml_output


MAX_XML_BYTES = MAX_XML_CHARACTERS * 4
MAX_EXPECTATIONS_BYTES = 32_768


def _read_bounded(path: Path, limit: int) -> bytes | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError:
        return None
    return data if len(data) <= limit else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml-file", type=Path, required=True)
    parser.add_argument("--expectations-file", type=Path)
    args = parser.parse_args(argv)

    xml_bytes = _read_bounded(args.xml_file, MAX_XML_BYTES)
    if xml_bytes is None:
        print(json.dumps({"status": "INPUT_UNAVAILABLE_OR_OVER_BUDGET"}))
        return 2
    try:
        xml_text = xml_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        print(json.dumps({"status": "UNSUPPORTED_ENCODING", "scope": "UTF-8 XML output only"}))
        return 2
    expectations = None
    if args.expectations_file is not None:
        raw = _read_bounded(args.expectations_file, MAX_EXPECTATIONS_BYTES)
        if raw is None:
            print(json.dumps({"status": "EXPECTATIONS_UNAVAILABLE_OR_OVER_BUDGET"}))
            return 2
        try:
            expectations = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print(json.dumps({"status": "INVALID_EXPECTATIONS"}))
            return 2
    result = evaluate_xml_output(xml_text, expectations)
    result["source_bytes_sha256"] = hashlib.sha256(xml_bytes).hexdigest()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
