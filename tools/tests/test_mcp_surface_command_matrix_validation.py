from tools.core.agent_surface_target_visibility import extract_target_file


def test_wrapped_actor_gateway_result_exposes_structured_affected_file() -> None:
    sample = {
        "body": """
        {
          "status": "PROPOSAL_CONFORMANT",
          "tool_result": "{\\"affected_scope\\": {\\"files\\": [\\"src/validation/a.ts\\"]}}"
        }
        """
    }

    assert extract_target_file(sample) == "src/validation/a.ts"
