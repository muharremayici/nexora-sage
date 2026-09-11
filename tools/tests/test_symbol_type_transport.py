from tools.engines.generate_atlas import _normalize_polyglot_symbol


def test_typescript_component_classifier_hint_survives_atlas_normalization():
    normalized = _normalize_polyglot_symbol(
        {
            "name": "DataProvider",
            "type": "Arrow",
            "canonicalSymbolType": "function",
            "signature": "() => object",
        },
        "export const DataProvider = () => ({ load: true });\n",
        language="typescript",
    )

    assert normalized["type"] == "Arrow"
    assert normalized["canonical_symbol_type"] == "function"
