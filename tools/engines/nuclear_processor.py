import os
import re
import hashlib
import sys
import json
from collections import defaultdict

from tools.core.atlas_io import load_atlas_data
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.config import ROOT, RAW_DIR, SKIP_DIRS, normalize_path, save_json_atomic, DOCTRINE
from tools.core.doctrine_contract import require_doctrine_mapping, require_doctrine_path
from tools.core.json_io import load_json_file
from tools.core.artifact_validator import ensure_valid_payload
from tools.core.genome_io import load_genome_data
from tools.core.projects_registry import canonical_project_name, resolve_runtime_projects
from tools.core.semantic_tokens import LEAK_KEYWORDS
from tools.core.source_files import is_analysis_source_file
from tools.core.logger import logger
from pathlib import Path

logger.info("!! NUCLEAR ENGINE V15.6 LOADED !!")

# Paths
CODE_MAPS_DIR = Path(__file__).resolve().parent.parent.parent

# Force UTF-8 for Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from tools.core.language_registry import language_for_extension

class NanometricParser:
    def __init__(self):
        # Compiled patterns cache
        self._compiled_cache = {}
        
        # Load fallback patterns from JSON
        self.fallback_patterns_dict = self.load_fallback_patterns()
        
        # Precompile and cache TypeScript for default/fallback/backward compatibility
        ts_compiled = self.get_patterns_for_language("typescript")
        self.patterns = ts_compiled['patterns']
        self.method_pattern = ts_compiled['method_pattern']
        self.symbol_usage_pattern = ts_compiled['symbol_usage_pattern']
        self.import_pattern = ts_compiled['import_pattern']
        self.import_pattern_v1 = ts_compiled['import_pattern_v1']
        
        self.leak_keywords = LEAK_KEYWORDS
        # [Architect Protocol] Global Significance Defaults
        self.low_signal_stoplist = set(require_doctrine_path("low_signal_tokens", expected_type=list))
        
        heuristics = require_doctrine_mapping("analysis_heuristics")
        sig_config = heuristics.get("symbol_significance", {})
        
        # Override with specific heuristics if present
        if sig_config.get("low_signal_tokens"):
            self.low_signal_stoplist.update(sig_config.get("low_signal_tokens", []))
            
        self.min_name_length = sig_config.get("min_name_length", 3)
        self.ignored_prefixes = sig_config.get("ignored_prefixes", ["use"])

    def load_fallback_patterns(self):
        patterns_file = CODE_MAPS_DIR / "config" / "language_fallback_patterns.json"
        if not patterns_file.exists():
            raise ValueError(f"[NUCLEAR] Mandatory config file 'language_fallback_patterns.json' is missing at: {patterns_file}")
        
        try:
            with open(patterns_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            raise ValueError(f"[NUCLEAR] Failed to parse JSON config file 'language_fallback_patterns.json': {e}")
            
        if not isinstance(data, dict) or "languages" not in data or not isinstance(data["languages"], dict):
            raise ValueError("[NUCLEAR] Invalid structure in 'language_fallback_patterns.json'. Expected root dictionary with 'languages' key.")
            
        patterns_dict = {}
        for lang, payload in data["languages"].items():
            if isinstance(payload, dict):
                patterns_dict[lang.lower()] = payload
                
        # Validate that we have at least typescript and javascript patterns
        required_langs = ["typescript", "javascript"]
        for rl in required_langs:
            if rl not in patterns_dict:
                raise ValueError(f"[NUCLEAR] Missing mandatory language patterns for '{rl}' in language_fallback_patterns.json!")
                
        return patterns_dict

    def get_patterns_for_language(self, language):
        language = str(language or "typescript").lower()
        if language in self._compiled_cache:
            return self._compiled_cache[language]
            
        config = self.fallback_patterns_dict.get(language)
        if not config:
            config = self.fallback_patterns_dict.get("typescript")
            
        compiled = {
            'patterns': {},
            'method_pattern': None,
            'symbol_usage_pattern': None,
            'import_pattern': None,
            'import_pattern_v1': None
        }
        
        raw_patterns = config.get("patterns", {})
        for name, pat in raw_patterns.items():
            compiled['patterns'][name] = re.compile(pat)
            
        meth = config.get("method_pattern")
        if meth:
            compiled['method_pattern'] = re.compile(meth)
            
        sym = config.get("symbol_usage_pattern", r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")
        compiled['symbol_usage_pattern'] = re.compile(sym)
        
        imp = config.get("import_pattern")
        if imp:
            compiled['import_pattern'] = re.compile(imp, re.MULTILINE)
            
        imp_v1 = config.get("import_pattern_v1")
        if imp_v1:
            compiled['import_pattern_v1'] = re.compile(imp_v1)
            
        self._compiled_cache[language] = compiled
        return compiled

    def extract_dependencies(self, content, language="typescript"):
        """Extracts unique imports and determines external vs internal coupling."""
        compiled = self.get_patterns_for_language(language)
        import_pat = compiled['import_pattern_v1']
        imports = []
        if import_pat:
            raw_matches = import_pat.findall(content)
            for m in raw_matches:
                if isinstance(m, tuple):
                    for group in m:
                        if group:
                            for part in re.split(r'[\s,]+', group):
                                if part.strip():
                                    imports.append(part.strip())
                else:
                    if m.strip():
                        imports.append(m.strip())
        deps = {
            'external': [],
            'internal': [],
            'studio': [],
        }
        for imp in imports:
            if imp.startswith('.'):
                deps['internal'].append(imp)
            elif imp.startswith('@/'):
                deps['studio'].append(imp)
            else:
                deps['external'].append(imp)
        return deps

    def determine_architectural_meta(self, rel_path):
        from tools.core.studio_resolver import studio_from_generic_path
        path_lower = rel_path.lower().replace('\\', '/')
        studio, confidence = studio_from_generic_path(rel_path)
        
        heuristics = require_doctrine_mapping("layer_heuristics")
        defaults = heuristics.get("defaults", {})
        
        fsd_layer = defaults.get("fsd", "Global")
        for trigger, label in heuristics.get("fsd", []):
            if trigger in path_lower:
                fsd_layer = label
                break
        
        hex_segment = defaults.get("hexagonal", "Cross-Cutting")
        for trigger, label in heuristics.get("hexagonal", []):
            if trigger in path_lower:
                hex_segment = label
                break
        
        return {'studio': studio, 'fsd': fsd_layer, 'hexagonal': hex_segment}

    def predict_target_path(self, block_name, meta):
        from tools.core.fractal_policy import get_main_module_base_prefix
        from tools.core.projects_registry import STUDIO_TO_MODULE

        studio_slug = meta['studio'].lower().split()[0]
        module_name = STUDIO_TO_MODULE.get(studio_slug, studio_slug)
        segment_slug = "application" if "Application" in meta['hexagonal'] else "domain" if "Domain" in meta['hexagonal'] else "infra" if "Infrastructure" in meta['hexagonal'] else "shared"
        
        subfolder = ""
        heuristics = require_doctrine_mapping("analysis_heuristics")
        path_rules = heuristics.get("path_prediction_rules", [])
        for rule in path_rules:
            triggers = rule.get("trigger", [])
            if any(t in block_name.lower() or t in block_name for t in triggers):
                subfolder = rule.get("subfolder", "")
                break

        return f"{get_main_module_base_prefix()}/{module_name}/{segment_slug}/{subfolder}{block_name}.ts"

    def is_significant_symbol(self, block):
        name = (block.get('name') or '').strip()
        block_type = (block.get('type') or '').strip()
        if not name:
            return False

        lowered = name.lower()
        if lowered in self.low_signal_stoplist:
            return False

        if len(name) < self.min_name_length and not any(name.startswith(p) for p in self.ignored_prefixes):
            return False

        if block_type in {'Variable', 'ExportedVariable', 'Arrow', 'Arrow/Hook', 'Type', 'TypeDefinition', 'Hook', 'Class', 'Function', 'DefaultExport', 'ProxyExport'}:
            if len(name) <= 2 and not any(name.startswith(p) for p in self.ignored_prefixes):
                return False
            if len(name) <= 3 and lowered == name and not any(name.startswith(p) for p in self.ignored_prefixes):
                return False
        
        # [Architect Protocol] __file_meta__ is internal telemetry, not a project symbol
        if name == '__file_meta__':
            return False

        return True

    def _normalize_structural_list(self, value):
        if not value:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []

    def _derive_signature_from_symbol(self, symbol):
        signature = str(symbol.get('signature') or '').strip()
        if signature:
            return signature

        member_details = symbol.get('member_details') or symbol.get('memberDetails') or []
        if member_details:
            parts = []
            for member in member_details:
                if isinstance(member, dict):
                    parts.append(str(member.get('signature') or member.get('name') or '').strip())
            parts = [part for part in parts if part]
            if parts:
                return "|".join(parts)

        members = self._normalize_structural_list(symbol.get('members'))
        if members:
            return "|".join(members)
        return ""

    def _structural_tags_from_symbol(self, symbol):
        tags = []
        export_kind = str(symbol.get('export_kind') or symbol.get('exportKind') or '').strip()
        if export_kind:
            tags.append(f"export:{export_kind}")

        for modifier in self._normalize_structural_list(symbol.get('modifiers')):
            tags.append(f"modifier:{modifier}")
        for parent in self._normalize_structural_list(symbol.get('extends')):
            tags.append(f"extends:{parent}")
        for contract in self._normalize_structural_list(symbol.get('implements')):
            tags.append(f"implements:{contract}")
        return tags

    def _normalize_member_details(self, value):
        if not isinstance(value, list):
            return []

        normalized = []
        for item in value:
            if not isinstance(item, dict):
                continue
            dependency_imports = [
                dep
                for dep in (item.get('dependencyImports') or item.get('dependency_imports') or [])
                if isinstance(dep, dict)
            ]
            side_effect_imports = [
                dep
                for dep in (item.get('sideEffectImports') or item.get('side_effect_imports') or [])
                if isinstance(dep, dict)
            ]
            side_effect_calls = [
                dep
                for dep in (item.get('sideEffectCalls') or item.get('side_effect_calls') or [])
                if isinstance(dep, dict)
            ]
            normalized.append({
                'name': str(item.get('name') or '').strip(),
                'kind': str(item.get('kind') or '').strip(),
                'signature': str(item.get('signature') or '').strip(),
                'optional': bool(item.get('optional', False)),
                'readonly': bool(item.get('readonly', False)),
                'static': bool(item.get('static', False)),
                'dependencies': self._normalize_structural_list(item.get('dependencies')),
                'dependency_imports': dependency_imports,
                'ui_dependencies': self._normalize_structural_list(item.get('uiDependencies') or item.get('ui_dependencies')),
                'dynamic_imports': self._normalize_structural_list(item.get('dynamicImports') or item.get('dynamic_imports')),
                'architectural_markers': self._normalize_structural_list(item.get('architecturalMarkers') or item.get('architectural_markers')),
                'side_effect_markers': self._normalize_structural_list(item.get('sideEffectMarkers') or item.get('side_effect_markers')),
                'side_effect_imports': side_effect_imports,
                'side_effect_calls': side_effect_calls,
            })
        return normalized

    def _collect_member_usage(self, member_details):
        member_dependencies = set()
        member_imported_contracts = set()
        member_ui_dependencies = set()
        member_dynamic_imports = set()
        member_architectural_markers = set()
        member_side_effect_markers = set()
        member_side_effect_imports = set()
        member_side_effect_calls = set()

        for member in member_details:
            if not isinstance(member, dict):
                continue
            for dependency in self._normalize_structural_list(member.get('dependencies')):
                member_dependencies.add(dependency)
            for item in member.get('dependency_imports', []):
                if not isinstance(item, dict):
                    continue
                local_name = str(item.get('localName') or '').strip()
                source = str(item.get('source') or '').strip()
                if local_name and source:
                    member_imported_contracts.add(f"{local_name}->{source}")
            for item in self._normalize_structural_list(member.get('ui_dependencies')):
                member_ui_dependencies.add(item)
            for item in self._normalize_structural_list(member.get('dynamic_imports')):
                member_dynamic_imports.add(item)
            for item in self._normalize_structural_list(member.get('architectural_markers')):
                member_architectural_markers.add(item)
            for item in self._normalize_structural_list(member.get('side_effect_markers')):
                member_side_effect_markers.add(item)
            for item in member.get('side_effect_imports', []):
                if not isinstance(item, dict):
                    continue
                local_name = str(item.get('localName') or '').strip()
                source = str(item.get('source') or '').strip()
                if local_name and source:
                    member_side_effect_imports.add(f"{local_name}->{source}")
            for item in member.get('side_effect_calls', []):
                if not isinstance(item, dict):
                    continue
                marker = str(item.get('marker') or '').strip()
                expression = str(item.get('expression') or '').strip()
                trigger_kind = str(item.get('triggerKind') or '').strip()
                local_name = str(item.get('localName') or '').strip()
                source = str(item.get('source') or '').strip()
                if not marker or not expression:
                    continue
                descriptor = f"{marker}:{expression}"
                if trigger_kind:
                    descriptor = f"{descriptor} [{trigger_kind}]"
                if local_name and source:
                    descriptor = f"{descriptor} -> {local_name}->{source}"
                member_side_effect_calls.add(descriptor)

        return (
            sorted(member_dependencies),
            sorted(member_imported_contracts),
            sorted(member_ui_dependencies),
            sorted(member_dynamic_imports),
            sorted(member_architectural_markers),
            sorted(member_side_effect_markers),
            sorted(member_side_effect_imports),
            sorted(member_side_effect_calls),
        )

    def _enrich_atlas_symbol(self, symbol, rel_path, meta, atlas_file_data, atlas_deps):
        nr = dict(symbol)
        atlas_features = list(atlas_file_data.get('features', []))
        atlas_leaks = [feat for feat in atlas_features if str(feat).startswith('Leak:')]
        coupling_score = len(atlas_file_data.get('internal_deps', []) or [])
        semantic_total = float(atlas_file_data.get('semantic_total', 0) or 0)

        extends = self._normalize_structural_list(nr.get('extends'))
        implements = self._normalize_structural_list(nr.get('implements'))
        members = self._normalize_structural_list(nr.get('members'))
        member_details = self._normalize_member_details(nr.get('member_details') or nr.get('memberDetails') or [])
        modifiers = self._normalize_structural_list(nr.get('modifiers'))
        exported_names = self._normalize_structural_list(
            nr.get('exported_names') or nr.get('exportedNames')
        )
        dependency_imports = list(nr.get('dependency_imports') or nr.get('dependencyImports') or [])
        dynamic_imports = self._normalize_structural_list(nr.get('dynamic_imports') or nr.get('dynamicImports'))
        ui_dependencies = self._normalize_structural_list(nr.get('ui_dependencies') or nr.get('uiDependencies'))
        architectural_markers = self._normalize_structural_list(nr.get('architectural_markers') or nr.get('architecturalMarkers'))
        (
            member_dependencies,
            member_imported_contracts,
            member_ui_dependencies,
            member_dynamic_imports,
            member_architectural_markers,
            member_side_effect_markers,
            member_side_effect_imports,
            member_side_effect_calls,
        ) = self._collect_member_usage(member_details)

        nr['meta'] = meta
        nr['project'] = "UNKNOWN"
        nr['file'] = rel_path
        nr['target_path_prediction'] = self.predict_target_path(nr['name'], meta)
        nr['signature'] = self._derive_signature_from_symbol(nr)
        nr['dna_short'] = (nr.get('dna') or '')[:16]
        nr['logic_dna'] = str(nr.get('logic_dna') or nr.get('logicDna') or nr.get('dna') or '')
        nr['logic_dna_short'] = nr['logic_dna'][:16]
        nr['semantic_signature'] = str(nr.get('semantic_signature') or nr.get('semanticSignature') or nr['signature'])
        nr['canonical_symbol_type'] = str(nr.get('canonical_symbol_type') or nr.get('canonicalSymbolType') or '').strip()
        if not nr['canonical_symbol_type']:
            from tools.core.language_agnostic_symbols import canonical_symbol_type
            from tools.core.language_registry import language_for_extension
            ext = os.path.splitext(rel_path)[1] if rel_path else ""
            language = language_for_extension(ext)
            nr['canonical_symbol_type'] = canonical_symbol_type(nr.get('type'), language=language)
        framework_tags = nr.get('framework_tags') or nr.get('frameworkTags') or []
        nr['framework_tags'] = self._normalize_structural_list(framework_tags)
        nr['normalization_profile'] = str(nr.get('normalization_profile') or nr.get('normalizationProfile') or '').strip()
        nr['semantic_depth'] = str(nr.get('semantic_depth') or nr.get('semanticDepth') or 'unavailable').strip()
        nr['logic_dna_kind'] = str(nr.get('logic_dna_kind') or nr.get('logicDnaKind') or 'unavailable').strip()
        nr['normalization_confidence'] = str(nr.get('normalization_confidence') or nr.get('normalizationConfidence') or 'none').strip()
        nr['parser_kind'] = str(nr.get('parser_kind') or nr.get('parserKind') or 'unknown').strip()
        nr['parser_version'] = str(nr.get('parser_version') or nr.get('parserVersion') or 'unknown').strip()
        nr['density'] = semantic_total
        nr['decoupling'] = max(0.0, 100.0 - (len(atlas_leaks) * 5))
        nr['coupling'] = coupling_score
        nr['leaks'] = atlas_leaks
        nr['deps'] = atlas_deps
        nr['imports_at_source'] = list((atlas_file_data.get('imports', []) or [])[:5])
        nr['symbols_referenced'] = list(nr.get('dependencies', []))
        nr['dependency_imports'] = dependency_imports
        nr['imported_contracts'] = sorted(
            {
                f"{item.get('localName')}->{item.get('source')}"
                for item in dependency_imports
                if isinstance(item, dict) and item.get('localName') and item.get('source')
            }
        )
        nr['member_dependencies'] = member_dependencies
        nr['member_imported_contracts'] = member_imported_contracts
        nr['member_ui_dependencies'] = member_ui_dependencies
        nr['member_dynamic_imports'] = member_dynamic_imports
        nr['member_architectural_markers'] = member_architectural_markers
        nr['member_side_effect_markers'] = member_side_effect_markers
        nr['member_side_effect_imports'] = member_side_effect_imports
        nr['member_side_effect_calls'] = member_side_effect_calls
        nr['dynamic_imports'] = dynamic_imports
        nr['ui_dependencies'] = ui_dependencies
        nr['architectural_markers'] = architectural_markers
        nr['exported'] = bool(nr.get('exported', False))
        nr['export_kind'] = str(nr.get('export_kind') or nr.get('exportKind') or 'local')
        nr['runtime_contract'] = bool(nr.get('runtime_contract', nr.get('runtimeContract', False)))
        nr['runtime_contract_kind'] = str(nr.get('runtime_contract_kind') or nr.get('contractKind') or '').strip()
        nr['modifiers'] = modifiers
        nr['extends'] = extends
        nr['implements'] = implements
        nr['members'] = members
        nr['member_details'] = member_details
        nr['exported_names'] = exported_names
        nr['structural_tags'] = self._structural_tags_from_symbol(nr)
        nr['structural_tags'].extend([f"dynamic:{item}" for item in dynamic_imports])
        nr['structural_tags'].extend([f"ui:{item}" for item in ui_dependencies])
        nr['structural_tags'].extend([f"arch:{item}" for item in architectural_markers])
        if nr['runtime_contract']:
            nr['structural_tags'].append('contract:runtime')
        if nr['runtime_contract_kind']:
            nr['structural_tags'].append(f"contractkind:{nr['runtime_contract_kind']}")
        nr['structural_tags'].extend([f"memberdep:{item}" for item in member_dependencies])
        nr['structural_tags'].extend([f"canonical:{nr['canonical_symbol_type']}"])
        nr['structural_tags'].extend([f"framework:{item}" for item in nr['framework_tags']])
        nr['structural_tags'].extend([f"memberui:{item}" for item in member_ui_dependencies])
        nr['structural_tags'].extend([f"memberdynamic:{item}" for item in member_dynamic_imports])
        nr['structural_tags'].extend([f"memberarch:{item}" for item in member_architectural_markers])
        nr['structural_tags'].extend([f"membersideeffect:{item}" for item in member_side_effect_markers])
        nr['structural_tags'].extend([f"membersideimport:{item}" for item in member_side_effect_imports])
        nr['structural_tags'].extend([f"membersidecall:{item}" for item in member_side_effect_calls])
        nr['structural_tags'] = sorted(set(nr['structural_tags']))
        nr['contract_edges'] = sorted(set(extends + implements))
        nr['contract_edges'] = sorted(set(nr['contract_edges'] + member_imported_contracts + member_side_effect_imports))
        nr['member_count'] = len(members)
        return nr

    def _deps_from_atlas(self, atlas_file_data):
        deps = {
            'external': [],
            'internal': [],
            'studio': [],
        }
        if not atlas_file_data:
            return deps

        for imp in atlas_file_data.get('imports', []) or []:
            source = imp if isinstance(imp, str) else imp.get('source', '')
            if not source:
                continue
            if source.startswith('.'):
                deps['internal'].append(source)
            elif source.startswith('@/') or source.startswith('src/'):
                deps['studio'].append(source)
            else:
                deps['external'].append(source)

        for key in deps:
            deps[key] = sorted(set(deps[key]))
        return deps

    def sequence_nanometric_blocks(self, content, rel_path, fpath=None, atlas_file_data=None):
        blocks = []
        meta = self.determine_architectural_meta(rel_path)
        
        # [PHASE 14] Sovereign Hybrid Sequencing: PREFER Atlas-sourced AST data
        if atlas_file_data and atlas_file_data.get('symbols'):
            atlas_deps = self._deps_from_atlas(atlas_file_data)
            for sym in atlas_file_data['symbols']:
                if isinstance(sym, dict) and sym.get('name'):
                    nr = self._enrich_atlas_symbol(sym, rel_path, meta, atlas_file_data, atlas_deps)
                    if self.is_significant_symbol(nr):
                        blocks.append(nr)
            if blocks: return blocks # Universal Succession!

        if content is None:
            return []

        # Determine language from extension
        ext = ""
        if rel_path:
            ext = os.path.splitext(rel_path)[1]
        elif fpath:
            ext = os.path.splitext(fpath)[1]
        language = language_for_extension(ext)
        compiled = self.get_patterns_for_language(language)

        imports = []
        import_pat = compiled['import_pattern']
        if import_pat:
            raw_matches = import_pat.findall(content)
            for m in raw_matches:
                if isinstance(m, tuple):
                    for group in m:
                        if group and group.strip():
                            imports.append(group.strip())
                else:
                    if m.strip():
                        imports.append(m.strip())

        file_deps = self.extract_dependencies(content, language)
        coupling_score = len(file_deps['internal']) + len(file_deps['studio'])
        
        for btype, pattern in compiled['patterns'].items():
            for match in pattern.finditer(content):
                name = match.group(1)
                start_offset = match.start()
                start_line = content[:start_offset].count('\n') + 1
                
                brace_start = -1
                brace_end = -1
                body = ""
                
                if language == "python":
                    # Python block: find next class/def or unindented line at the same level
                    lines = content[match.end():].splitlines()
                    block_lines = []
                    for line in lines:
                        if not line.strip():
                            block_lines.append(line)
                            continue
                        leading_ws = len(line) - len(line.lstrip())
                        if leading_ws == 0:
                            break
                        block_lines.append(line)
                    body = "\n".join(block_lines)
                    end_line = start_line + len(block_lines)
                else:
                    brace_start = content.find('{', match.end())
                    if brace_start != -1 and (brace_start - match.end() <= 1000):
                        stack = 0
                        for j in range(brace_start, len(content)):
                            if content[j] == '{': stack += 1
                            elif content[j] == '}':
                                stack -= 1
                                if stack == 0:
                                    brace_end = j
                                    break
                        if brace_end != -1:
                            body = content[brace_start:brace_end+1]
                            end_line = content[:brace_end].count('\n') + 1

                if body or (language == "python" and start_line):
                    heuristics = require_doctrine_mapping("analysis_heuristics")
                    scoring_config = heuristics.get("complexity_scoring", {})
                    
                    dna = hashlib.sha256(re.sub(r'\s+', '', body).encode()).hexdigest()
                    logic_dna = dna
                    density_kws = scoring_config.get("keywords", ['if', 'else', 'await', 'async', 'Promise', 'map', 'filter', 'Manager', 'Orchestrator'])
                    density = sum(len(re.findall(r'\b' + kw + r'\b', body)) for kw in density_kws)
                    
                    # Nanometric Leak Audit
                    leaks = []
                    for cat, kws in self.leak_keywords.items():
                        for kw in kws:
                            if kw in body: leaks.append(kw)
                    
                    multiplier = scoring_config.get("leak_penalty_multiplier", 5.0)
                    decoupling = max(0.0, 100.0 - (len(leaks) * multiplier))
                    
                    params = match.group(2).strip() if len(match.groups()) > 1 and match.group(2) else ""
                    if btype == 'Class' and compiled['method_pattern']:
                        m = compiled['method_pattern'].findall(body)
                        params = "|".join([
                            f"{meth[0]}({meth[1]})" if isinstance(meth, tuple) and len(meth) > 1 else str(meth)
                            for meth in m
                        ])
                    
                    symbols = set(compiled['symbol_usage_pattern'].findall(body))
                    if name in symbols: symbols.remove(name)
                    
                    blocks.append({
                        'name': name,
                        'type': btype,
                        'dna': dna,
                        'dna_short': dna[:16],
                        'logic_dna': logic_dna,
                        'logic_dna_short': logic_dna[:16],
                        'semantic_signature': params,
                        'canonical_symbol_type': 'class' if btype == 'Class' else ('interface' if btype == 'Interface' else ('type' if btype == 'Type' else 'function')),
                        # Regex fallback must preserve the Genome cache contract too.
                        # Empty values are honest fallback evidence; absent fields create a stale-loop.
                        'export_kind': 'local',
                        'runtime_contract': '',
                        'runtime_contract_kind': '',
                        'modifiers': [],
                        'extends': [],
                        'implements': [],
                        'members': [],
                        'member_details': [],
                        'dynamic_imports': [],
                        'ui_dependencies': [],
                        'architectural_markers': [],
                        'structural_tags': [],
                        'contract_edges': [],
                        'member_count': 0,
                        'member_dependencies': [],
                        'member_imported_contracts': [],
                        'member_ui_dependencies': [],
                        'member_dynamic_imports': [],
                        'member_architectural_markers': [],
                        'member_side_effect_markers': [],
                        'member_side_effect_imports': [],
                        'member_side_effect_calls': [],
                        'framework_tags': [],
                        'normalization_profile': 'regex_fallback_v1',
                        'semantic_depth': 'unavailable',
                        'logic_dna_kind': 'unavailable',
                        'normalization_confidence': 'none',
                        'parser_kind': 'regex_fallback',
                        'parser_version': 'v1',
                        'density': density,
                        'decoupling': decoupling,
                        'coupling': coupling_score,
                        'deps': file_deps,
                        'leaks': leaks,
                        'signature': params,
                        'symbols_referenced': list(symbols),
                        'meta': meta,
                        'source_lines': f"L{start_line}-L{end_line}",
                        'target_path_prediction': self.predict_target_path(name, meta),
                        'imports_at_source': imports[:5]
                    })
        return [block for block in blocks if self.is_significant_symbol(block)]

class NanometricKernel:
    def __init__(self):
        self.parser = NanometricParser()
        self.projects = resolve_runtime_projects(ROOT)
        self.discovery = {}
        
        # Load Atlas for SSOT sourcing
        # [Phase 6] RAM Persistence Optimization: Check orchestrator cache first
        from tools.engines.generate_atlas import GLOBAL_ATLAS_CACHE as GAC
        if GAC:
            self.atlas = GAC
            logger.info("[FAST] [DNA] Shared Atlas Cache Linked.")
        else:
            self.atlas = load_atlas_data()
            if self.atlas:
                logger.info(f"[DNA] Loaded Atlas from disk ({len(self.atlas)} projects).")

    @staticmethod
    def _apply_scoped_fields(block, default_project=None, default_file=None):
        project = str(block.get('project') or default_project or 'UNKNOWN')
        rel_file = str(block.get('file') or default_file or '')
        workspace_rel = str(block.get('workspace_rel') or '').replace("\\", "/").strip("/")
        if rel_file:
            block['scoped_file'] = f"{project}::{rel_file}"
        if workspace_rel:
            block['scoped_workspace_rel'] = f"{project}::{workspace_rel}"
        return block

    def _lookup_atlas_entry(self, pkey, rel):
        search_pkey = str(pkey).upper()
        atlas_project = next((v for k, v in self.atlas.items() if k.upper() == search_pkey), {})

        lookup_rel = rel.replace("\\", "/").strip("/")
        atlas_entry = atlas_project.get("files", {}).get(lookup_rel)
        if not atlas_entry:
            alt_lookup = lookup_rel.replace("src/", "", 1) if lookup_rel.startswith("src/") else "src/" + lookup_rel
            atlas_entry = atlas_project.get("files", {}).get(alt_lookup)
        if not atlas_entry:
            atlas_entry = next(
                (
                    v for k, v in atlas_project.get("files", {}).items()
                    if k.lower().endswith(lookup_rel.lower()) or lookup_rel.lower().endswith(k.lower())
                ),
                None,
            )
        return atlas_entry

    def _atlas_rel_for_change(self, pkey, rel):
        search_pkey = str(pkey).upper()
        atlas_project = next((v for k, v in self.atlas.items() if k.upper() == search_pkey), {})
        files = atlas_project.get("files", {}) if isinstance(atlas_project, dict) else {}
        lookup_rel = str(rel or "").replace("\\", "/").strip("/")
        if isinstance(files, dict) and lookup_rel in files:
            return lookup_rel
        if isinstance(files, dict):
            for atlas_rel, file_meta in files.items():
                if not isinstance(file_meta, dict):
                    continue
                candidates = {
                    str(file_meta.get("workspace_rel") or "").replace("\\", "/").strip("/"),
                    str(file_meta.get("repo_relative_path") or "").replace("\\", "/").strip("/"),
                    str(file_meta.get("target_ref") or "").split("::", 1)[-1].replace("\\", "/").strip("/"),
                }
                if lookup_rel in candidates:
                    return str(atlas_rel).replace("\\", "/").strip("/")
        return lookup_rel

    def _cached_block_contract_gap(self, cached_blocks, atlas_entry):
        if not cached_blocks:
            return "missing_cached_blocks"
        from tools.engines.generate_atlas import AST_CONTRACT_VERSION

        current_contract_version = AST_CONTRACT_VERSION
        if atlas_entry and atlas_entry.get('symbols'):
            contract_version = str(atlas_entry.get('ast_contract_version') or '').strip()
            if contract_version:
                current_contract_version = contract_version

        required_fields = ('export_kind', 'runtime_contract', 'runtime_contract_kind', 'modifiers', 'extends', 'implements', 'members', 'member_details', 'dynamic_imports', 'ui_dependencies', 'architectural_markers', 'structural_tags', 'contract_edges', 'member_count', 'member_dependencies', 'member_imported_contracts', 'member_ui_dependencies', 'member_dynamic_imports', 'member_architectural_markers', 'member_side_effect_markers', 'member_side_effect_imports', 'member_side_effect_calls', 'logic_dna', 'semantic_signature', 'canonical_symbol_type', 'framework_tags', 'normalization_profile', 'semantic_depth', 'logic_dna_kind', 'normalization_confidence', 'parser_kind', 'parser_version')
        for block in cached_blocks:
            if str(block.get('ast_contract_version') or '').strip() != current_contract_version:
                return "ast_contract_version_mismatch"
            missing_field = next((field for field in required_fields if field not in block), None)
            if missing_field:
                return (
                    f"missing_required_field:{missing_field}:"
                    f"file={block.get('file') or 'unknown'}:"
                    f"symbol={block.get('name') or 'unknown'}:"
                    f"type={block.get('type') or 'unknown'}"
                )
        return None

    def _cached_blocks_are_current(self, cached_blocks, atlas_entry):
        return self._cached_block_contract_gap(cached_blocks, atlas_entry) is None

    def _project_cache_contract(self, pkey, file_db):
        entries = [(p, f, blocks) for (p, f), blocks in file_db.items() if p == pkey]
        if not entries:
            return entries, "missing_project_cache_entries"
        for _, rel_file, blocks in entries:
            gap = self._cached_block_contract_gap(blocks, self._lookup_atlas_entry(pkey, rel_file))
            if gap:
                return entries, gap
        return entries, ""

    def run(self, stale_projects=None, changed_files=None, use_cache=True):
        logger.info("--- [PHASE 4] NUCLEAR SURGERY V12 (Surgical-Only) ---")
        current_project_keys = set(self.projects.keys())
        
        file_db = defaultdict(list)
        if use_cache:
            try:
                old_data = load_genome_data()
                for name, occs in old_data.items():
                    for o in occs:
                        pkey = o.get('project', 'UNKNOWN')
                        if pkey not in current_project_keys:
                            continue
                        frel = o.get('file', 'UNKNOWN')
                        file_db[(pkey, frel)].append(o)
                logger.info(f"[FAST] [CACHE] Nuclear Lift: Loaded existing genome data.")
            except Exception as e:
                logger.warning(f"Failed to load old genome for caching: {e}")

        universe = defaultdict(list)

        for pkey, ppath in self.projects.items():
            logger.info(f"--- [DNA] ATTEMPTING PROJECT: {pkey} ---")
            if stale_projects is not None and pkey not in stale_projects:
                project_cache_entries, stale_reason = self._project_cache_contract(pkey, file_db)
                project_cache_current = not stale_reason

                if project_cache_current:
                    logger.info(f"[DNA] [SKIP] {pkey} is frozen.")
                    for p, f, blocks in project_cache_entries:
                        for b in blocks:
                            block = self._apply_scoped_fields(dict(b), default_project=p, default_file=f)
                            universe[block['name']].append(block)
                    continue

                logger.warning(
                    f"[DNA] [STALE] {pkey} cache is frozen but genome contract is stale; "
                    f"reason={stale_reason or 'unknown_contract_gap'}; rebuilding."
                )
                
            logger.info(f"[DNA] Atomic Sequencing Proj: {pkey}...")
            
            if changed_files:
                p_changes = [f for f in changed_files if f.startswith(f"{pkey}::")]
                
                # [FIX] If no changes detected in surgical scope AND no cache entries exist for this project,
                # fall back to a full walk to ensure it's not a missing baseline.
                has_cache = any(p == pkey for (p, f) in file_db.keys())
                
                if not p_changes and has_cache:
                     for (p, f), blocks in file_db.items():
                        if p == pkey:
                            for b in blocks:
                                block = self._apply_scoped_fields(dict(b), default_project=p, default_file=f)
                                universe[block['name']].append(block)
                     continue
                
                if p_changes:
                    logger.info(f"[LAUNCH] [SURGICAL] Only sequencing {len(p_changes)} changed files in {pkey}.")
                    changed_rel_paths = {self._atlas_rel_for_change(pkey, f.split("::", 1)[1]) for f in p_changes}
                    for (p, frel), blocks in file_db.items():
                        if p == pkey and frel not in changed_rel_paths:
                            for b in blocks:
                                block = self._apply_scoped_fields(dict(b), default_project=p, default_file=frel)
                                universe[block['name']].append(block)

                    for f_node in p_changes:
                        rel = self._atlas_rel_for_change(pkey, f_node.split("::", 1)[1])
                        fpath = os.path.join(ppath, rel.replace("/", os.sep))
                        if not os.path.exists(fpath): continue
                        self._process_file(fpath, rel, pkey, universe)

                    continue
                # Else: (not p_changes and not has_cache) -> fall through to os.walk

            logger.info(f"[DNA] Sequencing committed Atlas file universe for: {pkey}")
            walk_count = 0
            atlas_project = self.atlas.get(pkey, {}) if isinstance(self.atlas, dict) else {}
            atlas_files = atlas_project.get('files', {}) if isinstance(atlas_project, dict) else {}
            for rel in sorted(atlas_files):
                if not is_analysis_source_file(rel):
                    continue
                fpath = os.path.join(ppath, rel.replace('/', os.sep))
                walk_count += 1
                if walk_count % 100 == 0:
                    logger.info(f"[DNA] Processing Atlas file batch: {walk_count} files reached...")
                self._process_file(fpath, rel, pkey, universe, file_db)
            logger.info(f"[DNA] Finished Atlas sequencing. Total committed source files: {walk_count}")


        # [Phase 6] Final Export
        sanitized_universe = self._sanitize_universe(universe, current_project_keys)
        # TELEMETRY: Genomic Pulse
        total_symbols = sum(len(occs) for occs in sanitized_universe.values())
        logger.info(f"[DNA] GENOMIC PULSE: {total_symbols} total symbols captured across projects.")
        
        if total_symbols == 0:
            logger.error("[CRITICAL] Genomic Universe is empty! Verification of Atlas-first path matching required.")
            
        self.export_nanometric_json(sanitized_universe)
        self.export_surgical_discovery(sanitized_universe)

    def _process_file(self, fpath, rel, pkey, universe, file_db=None):
        try:
            f_stat = os.stat(fpath)
            f_mtime = f_stat.st_mtime
        except (OSError, PermissionError): 
            f_mtime = -1

        atlas_entry = self._lookup_atlas_entry(pkey, rel)

        if file_db:
            cached_blocks = file_db.get((pkey, rel))
            if cached_blocks and cached_blocks[0].get('mtime') == f_mtime and self._cached_blocks_are_current(cached_blocks, atlas_entry):
                for b in cached_blocks:
                    block = self._apply_scoped_fields(dict(b), default_project=pkey, default_file=rel)
                    universe[block['name']].append(block)
                return

        # [PHASE 9] Symbiotic Test Link
        from tools.core.config import ROOT
        try:
            workspace_rel = str(Path(fpath).relative_to(ROOT)).replace("\\", "/")
        except (ValueError, TypeError):
            workspace_rel = os.path.relpath(fpath, str(ROOT)).replace("\\", "/")
            
        # [PHASE 9] Simplified: Use derived metadata instead of discovery
        test_path = None
        has_tests = False
        predicted_themes = []
        
        try:
            content = None
            file_hash = atlas_entry.get('hash') if atlas_entry else None
            if not (atlas_entry and atlas_entry.get('symbols')):
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                file_hash = hashlib.md5(content.encode()).hexdigest()

            blocks = self.parser.sequence_nanometric_blocks(content, rel, fpath=fpath, atlas_file_data=atlas_entry)
            for b in blocks:
                b['project'] = pkey
                b['file'] = rel
                b['mtime'] = f_mtime
                b['file_hash'] = file_hash
                b['ast_contract_version'] = atlas_entry.get('ast_contract_version') if atlas_entry else ""
                b['has_tests'] = has_tests
                b['test_path'] = test_path
                b['workspace_rel'] = workspace_rel
                b['scoped_file'] = f"{pkey}::{rel}"
                b['scoped_workspace_rel'] = f"{pkey}::{workspace_rel}"
                
                # Deterministic Theme Matching
                b['themes'] = [t for t in predicted_themes if t.lower() in b['name'].lower() or t.lower() in workspace_rel.lower()]
                if not b['themes']: b['themes'] = ["Standard Logic"]
                
                universe[b['name']].append(b)
        except Exception as e:
            logger.warning(f"Error parsing file {fpath}: {str(e)}")

    def _sanitize_universe(self, universe, allowed_projects):
        from tools.engines.generate_atlas import AST_CONTRACT_VERSION

        sanitized = defaultdict(list)
        # Normalize allowed projects to upper for immune cross-matching
        allowed_upper = {p.upper() for p in allowed_projects}
        
        for name, occs in universe.items():
            kept = []
            for o in occs:
                o_proj = str(o.get('project', '')).upper()
                if (not allowed_upper or o_proj in allowed_upper or o_proj == "UNKNOWN") and self.parser.is_significant_symbol(o):
                    kept.append(o)
            deduped_by_key = {}
            for occ in kept:
                key = (
                    occ.get('project'),
                    occ.get('file'),
                    occ.get('name'),
                    occ.get('type'),
                    occ.get('start'),
                    occ.get('end'),
                    occ.get('signature'),
                )
                current = deduped_by_key.get(key)
                if current is None:
                    deduped_by_key[key] = occ
                    continue

                def score(item):
                    return (
                        1 if str(item.get('ast_contract_version') or '').strip() == AST_CONTRACT_VERSION else 0,
                        1 if item.get('member_side_effect_calls') else 0,
                        len(item.get('member_details') or []),
                        len(item.get('structural_tags') or []),
                    )

                if score(occ) > score(current):
                    deduped_by_key[key] = occ
            deduped = list(deduped_by_key.values())
            if deduped:
                sanitized[name].extend(deduped)
        return sanitized
    def export_nanometric_json(self, universe):
        ensure_valid_payload("genome", universe)
        export_path = RAW_DIR / 'genome.json'
        save_json_atomic(export_path, universe)
        write_current_atlas_lineage(
            artifact_id="genome",
            producer="tools.engines.nuclear_processor",
            artifact_payload=universe,
            atlas=self.atlas,
        )
        logger.info(f"[OK] NANOMETRIC GENOME JSON EXPORTED: {export_path}")

    def export_surgical_discovery(self, universe):
        export_path = RAW_DIR / 'surgical_discovery.json'
        flat_data = []
        for name, occs in universe.items():
            for o in occs:
                flat_data.append(o)
        
        save_json_atomic(export_path, flat_data)
        write_current_atlas_lineage(
            artifact_id="surgical_discovery",
            producer="tools.engines.nuclear_processor",
            artifact_payload=flat_data,
            atlas=self.atlas,
        )
        logger.info(f"[OK] Surgical JSON Exported: {export_path}")

def run_nuclear(stale_projects=None, changed_files=None, use_cache=True):
    NanometricKernel().run(stale_projects=stale_projects, changed_files=changed_files, use_cache=use_cache)

if __name__ == "__main__":
    run_nuclear()
