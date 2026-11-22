"""Validate the registered unavailable metadata contract; never admit runtime work."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.core.compiler_preparation_comparison_authority import (
    PreparationComparisonAuthorityRefusal, validate_registered_contract,
)


def main():
    try:
        result = validate_registered_contract()
    except PreparationComparisonAuthorityRefusal as error:
        print(json.dumps({"status": "FAIL", "code": error.code, "stage": error.stage}))
        return 1
    print(json.dumps({"status": "PASS_UNAVAILABLE_METADATA_CONTRACT_NOT_RUNTIME_READINESS", "result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
