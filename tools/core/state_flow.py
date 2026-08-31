from __future__ import annotations


def summarize_state_flow_features(features) -> dict:
    values = list(features or [])
    query_keys = []
    query_key_refs = []
    query_key_dynamic = []
    mutation_keys = []
    mutation_key_refs = []
    mutation_key_dynamic = []
    has_tanstack_mutation = False
    client_actions = []
    zustand_consumers = []
    zustand_no_selector_calls = []
    zustand_broad_selector_calls = []
    react_external_store_consumers = []
    technologies = set()

    for feature in values:
        text = str(feature or "").strip()
        if not text:
            continue
        if text == "ZustandStore":
            technologies.add("zustand")
        elif text.startswith("Tech:selector:"):
            hook_name = text.split(":", 2)[2].strip()
            if hook_name == "useSyncExternalStore":
                react_external_store_consumers.append(hook_name)
                technologies.add("react-external-store")
                continue
            if hook_name:
                zustand_consumers.append(hook_name)
            technologies.add("zustand")
        elif text == "Tech:useSyncExternalStore":
            react_external_store_consumers.append("useSyncExternalStore")
            technologies.add("react-external-store")
        elif text.startswith("Zustand:NoSelector"):
            parts = text.split(":", 2)
            if len(parts) > 2 and parts[2].strip():
                hook_name = parts[2].strip()
                zustand_consumers.append(hook_name)
                zustand_no_selector_calls.append(hook_name)
            technologies.add("zustand")
        elif text.startswith("Zustand:BroadSelector"):
            parts = text.split(":", 2)
            if len(parts) > 2 and parts[2].strip():
                hook_name = parts[2].strip()
                zustand_consumers.append(hook_name)
                zustand_broad_selector_calls.append(hook_name)
            technologies.add("zustand")
        elif text.startswith("QueryKey:"):
            query_keys.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text.startswith("QueryKeyRef:"):
            query_key_refs.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text.startswith("QueryKeyDynamic:"):
            query_key_dynamic.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text.startswith("MutationKey:"):
            mutation_keys.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text.startswith("MutationKeyRef:"):
            mutation_key_refs.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text.startswith("MutationKeyDynamic:"):
            mutation_key_dynamic.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text == "TanStackMutation":
            has_tanstack_mutation = True
            technologies.add("tanstack-query")
        elif text.startswith("QueryClientAction:"):
            client_actions.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")

    return {
        "has_zustand_store": "ZustandStore" in values,
        "query_keys": sorted(set(query_keys)),
        "query_key_refs": sorted(set(query_key_refs)),
        "query_key_dynamic": sorted(set(query_key_dynamic)),
        "mutation_keys": sorted(set(mutation_keys)),
        "mutation_key_refs": sorted(set(mutation_key_refs)),
        "mutation_key_dynamic": sorted(set(mutation_key_dynamic)),
        "has_tanstack_mutation": has_tanstack_mutation,
        "client_actions": sorted(set(client_actions)),
        "zustand_consumers": sorted(set(zustand_consumers)),
        "zustand_no_selector_calls": sorted(set(zustand_no_selector_calls)),
        "zustand_broad_selector_calls": sorted(set(zustand_broad_selector_calls)),
        "react_external_store_consumers": sorted(set(react_external_store_consumers)),
        "technologies": sorted(technologies),
    }
