from tools.core.doctrine_contract import require_doctrine_path

SEMANTIC_TOKENS = require_doctrine_path("semantic_taxonomy", expected_type=dict)
ARCHITECTURAL_MARKERS = require_doctrine_path("architectural_markers", expected_type=list)
LEAK_KEYWORDS = require_doctrine_path("leak_detection", expected_type=dict)
CAPABILITY_KEYWORDS = require_doctrine_path("capability_discovery", expected_type=dict)


