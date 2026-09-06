from tools.core.path_identity import strip_current_directory_prefix


def test_strip_current_directory_prefix_preserves_path_identity():
    assert strip_current_directory_prefix("./src/App.tsx") == "src/App.tsx"
    assert strip_current_directory_prefix("././src/App.tsx") == "src/App.tsx"
    assert strip_current_directory_prefix(".github/workflows/quality-gate.yml") == ".github/workflows/quality-gate.yml"
    assert strip_current_directory_prefix("../outside.py") == "../outside.py"
