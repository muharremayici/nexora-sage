from tools.core.artifact_validator import validate_all_artifacts
from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.logger import logger


def run_artifact_contract_validator():
    results = validate_all_artifacts()
    failures = []

    if not results:
        failures.append(
            (
                "__artifact_contract_scope__",
                ["artifact contract validation checked zero artifacts; refusing empty PASS"],
            )
        )

    for artifact_name, errors in results.items():
        if errors:
            failures.append((artifact_name, errors))
            logger.error(f"Contract validation failed for {artifact_name}: {len(errors)} issue(s)")
            for err in errors[:10]:
                logger.error(f"  - {err}")
        else:
            logger.info(f"Artifact contract valid: {artifact_name}")

    payload = {
        "status": "FAIL" if failures else "PASS",
        "total_artifacts": len(results),
        "valid_artifacts": len(results) - len(failures),
        "invalid_artifacts": len(failures),
        "failures": [
            {
                "artifact": artifact_name,
                "errors": errors,
            }
            for artifact_name, errors in failures
        ],
    }
    save_json_atomic(RAW_DIR / "artifact_contract_validation.json", payload)

    if failures:
        failing = ", ".join(name for name, _ in failures)
        raise RuntimeError(f"Artifact contract validation failed: {failing}")


if __name__ == "__main__":
    run_artifact_contract_validator()
