const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const { Buffer } = require('buffer');
const crypto = require('crypto');

/**
 * Absolute Sovereign AST Sequencer v16.0
 * Fully decoupled from technology-specific heuristics.
 * Now driven exclusively by the Discovery Taxonomy.
 */

// 1. Dynamic Discovery Configuration Loading
const startupArgs = process.argv.slice(2);
const configIdx = startupArgs.indexOf('--config');
let DISCOVERY_CONFIG = {
    semantic_tokens: {},
    complexity_weights: {},
    architectural_markers: [],
    side_effect_taxonomy: { identifiers: {}, property_patterns: [] },
    framework_patterns: { redux_toolkit: [], react_query: [], query_client_actions: [], react_router: [] }
};

if (configIdx !== -1 && startupArgs[configIdx + 1]) {
    try {
        const configPath = startupArgs[configIdx + 1];
        if (fs.existsSync(configPath)) {
            const raw = fs.readFileSync(configPath, 'utf8');
            DISCOVERY_CONFIG = JSON.parse(raw);
        }
    } catch (e) {
        // Fallback to empty if loading fails
    }
}

let SEMANTIC_TOKENS = DISCOVERY_CONFIG.semantic_tokens || {};
let GEM_WEIGHTS = DISCOVERY_CONFIG.complexity_weights || {};
let ARCH_MARKERS = DISCOVERY_CONFIG.architectural_markers || [];
let SIDE_EFFECT_IDENTIFIERS = DISCOVERY_CONFIG.side_effect_taxonomy?.identifiers || {};
let SIDE_EFFECT_PROPERTY_PATTERNS = (DISCOVERY_CONFIG.side_effect_taxonomy?.property_patterns || []).map(p => [p.marker, p.patterns]);
let NORMALIZATION_PROFILE = 'typescript_react_v1';
let LANGUAGE_AGNOSTIC_SYMBOLS = {
    canonical_types: [],
    raw_type_map: {},
    framework_tags: { directives: {}, wrappers: {}, exports: {} },
    normalization_profiles: {},
};

let FRAMEWORK_CAPABILITIES = { ast_feature_rules: {} };

try {
    const symbolsConfigPath = path.resolve(__dirname, '..', '..', 'config', 'language_agnostic_symbols.json');
    if (fs.existsSync(symbolsConfigPath)) {
        LANGUAGE_AGNOSTIC_SYMBOLS = JSON.parse(fs.readFileSync(symbolsConfigPath, 'utf8'));
        NORMALIZATION_PROFILE = LANGUAGE_AGNOSTIC_SYMBOLS.default_profiles?.typescript || NORMALIZATION_PROFILE;
    }
} catch (_err) {
    // Keep fallback defaults; validation guards the config-backed production path.
}

try {
    const frameworkConfigPath = path.resolve(__dirname, '..', '..', 'config', 'framework_capabilities.json');
    if (fs.existsSync(frameworkConfigPath)) {
        FRAMEWORK_CAPABILITIES = JSON.parse(fs.readFileSync(frameworkConfigPath, 'utf8'));
    }
} catch (_err) {
    // The configured production path is validated separately; an empty rule set is fail-safe.
}

function configuredSourceMatches(source, rule) {
    const normalized = String(source || '');
    const exact = Array.isArray(rule.sources) ? rule.sources : [];
    const prefixes = Array.isArray(rule.source_prefixes) ? rule.source_prefixes : [];
    return exact.includes(normalized) || prefixes.some(prefix => normalized.startsWith(String(prefix)));
}

function configuredFeaturesFor(kind, source, name = '') {
    const rules = FRAMEWORK_CAPABILITIES.ast_feature_rules?.[kind] || [];
    const features = new Set();
    for (const rule of rules) {
        if (!rule || !configuredSourceMatches(source, rule)) continue;
        const names = Array.isArray(rule.names) ? rule.names : [];
        if (names.length && !names.includes(name)) continue;
        for (const feature of Array.isArray(rule.features) ? rule.features : []) {
            if (feature) features.add(String(feature));
        }
    }
    return features;
}

function escapeRegex(value) {
    return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function semanticTokenMatches(sourceCode, token) {
    const text = String(token || '');
    if (!text) return false;

    // Punctuation-bearing framework markers are intentionally literal snippets.
    if (/[^A-Za-z0-9_$]/.test(text)) {
        return sourceCode.includes(text);
    }

    // Plain tokens must match lexical boundaries. This prevents broad semantic
    // categories from firing on import/export, vite, within, etc.
    const pattern = new RegExp(`(^|[^A-Za-z0-9_$])${escapeRegex(text)}($|[^A-Za-z0-9_$])`, 'i');
    return pattern.test(sourceCode);
}

function literalPropertyName(property) {
    const name = property?.name;
    return name && (ts.isIdentifier(name) || ts.isStringLiteralLike(name)) ? name.text : '';
}

function xmlResponseMime(options) {
    if (!options || !ts.isObjectLiteralExpression(options)) return false;
    const headers = options.properties.find(property =>
        ts.isPropertyAssignment(property) && literalPropertyName(property).toLowerCase() === 'headers');
    if (!headers || !ts.isObjectLiteralExpression(headers.initializer)) return false;
    const contentType = headers.initializer.properties.find(property =>
        ts.isPropertyAssignment(property) && literalPropertyName(property).toLowerCase() === 'content-type');
    return !!contentType && ts.isStringLiteralLike(contentType.initializer)
        && /^(?:application|text)\/xml(?:\s*;|$)/i.test(contentType.initializer.text);
}

function xmlInterpolationContext(prefix) {
    if (prefix.lastIndexOf('<![CDATA[') > prefix.lastIndexOf(']]>')) return 'cdata';
    const open = prefix.lastIndexOf('<');
    const close = prefix.lastIndexOf('>');
    if (open > close) {
        const tag = prefix.slice(open + 1);
        const doubleQuotes = (tag.match(/"/g) || []).length;
        const singleQuotes = (tag.match(/'/g) || []).length;
        return /=\s*["'][^"']*$/.test(tag) && (doubleQuotes % 2 || singleQuotes % 2)
            ? 'attribute_value' : '';
    }
    if (close > open) {
        const tag = prefix.slice(0, close + 1).match(/<\/?[A-Za-z_][\w:.-]*(?:\s[^<>]*)?>$/);
        return tag && !tag[0].startsWith('</') ? 'element_text' : '';
    }
    return '';
}

function isUnshadowedResponse(identifier, symbolAt) {
    const declarations = symbolAt(identifier)?.declarations || [];
    // The single-file checker has no DOM library. An unresolved Response is
    // only a global-constructor candidate, never proof of runtime ownership.
    return !declarations.some(declaration => declaration.getSourceFile() === identifier.getSourceFile());
}

function isNodeFsWriter(call, importBindings, symbolAt) {
    const owner = call.expression.expression;
    if (!ts.isIdentifier(owner)) return false;
    const method = call.expression.name.text;
    const allowedSources = method === 'writeFileSync'
        ? ['fs', 'node:fs'] : ['fs', 'node:fs', 'fs/promises', 'node:fs/promises'];
    const declarations = symbolAt(owner)?.declarations || [];
    if (declarations.length !== 1) return false;
    const declaration = declarations[0];
    const binding = importBindings.get(owner.text);
    if (binding && ['namespace', 'default'].includes(binding.kind)
        && allowedSources.includes(binding.source)) {
        const clause = ts.isImportClause(declaration) ? declaration
            : ts.isNamespaceImport(declaration) ? declaration.parent : null;
        const importDecl = clause?.parent;
        return !!clause && !clause.isTypeOnly && ts.isImportDeclaration(importDecl)
            && importDecl.moduleSpecifier.text === binding.source
            && declaration.name?.text === owner.text;
    }
    if (!ts.isVariableDeclaration(declaration) || !ts.isIdentifier(declaration.name)
        || declaration.name.text !== owner.text
        || !ts.isVariableDeclarationList(declaration.parent)
        || !(declaration.parent.flags & ts.NodeFlags.Const)
        || declaration.getStart() >= call.getStart()) return false;
    const initializer = declaration.initializer;
    return !!initializer && ts.isCallExpression(initializer)
        && ts.isIdentifier(initializer.expression) && initializer.expression.text === 'require'
        && !symbolAt(initializer.expression)
        && initializer.arguments.length === 1
        && ts.isStringLiteralLike(initializer.arguments[0])
        && allowedSources.includes(initializer.arguments[0].text);
}

function isImportedXmlMimeHelper(call, importBindings, symbolAt) {
    if (!ts.isIdentifier(call.expression) || call.arguments.length < 3) return false;
    const binding = importBindings.get(call.expression.text);
    if (!binding || !['named', 'default'].includes(binding.kind)) return false;
    const declarations = symbolAt(call.expression)?.declarations || [];
    if (declarations.length !== 1) return false;
    const declaration = declarations[0];
    let clause = null;
    if (ts.isImportSpecifier(declaration) && !declaration.isTypeOnly) {
        clause = ts.isNamedImports(declaration.parent) ? declaration.parent.parent : null;
    } else if (ts.isImportClause(declaration)) {
        clause = declaration;
    }
    const importDecl = clause?.parent;
    if (!clause || clause.isTypeOnly || !ts.isImportDeclaration(importDecl)
        || !ts.isStringLiteralLike(importDecl.moduleSpecifier)
        || importDecl.moduleSpecifier.text !== binding.source
        || declaration.name?.text !== call.expression.text) return false;
    const literals = call.arguments.filter(ts.isStringLiteralLike).map(arg => arg.text);
    return literals.some(value => /^(?:application|text)\/xml(?:\s*;|$)/i.test(value))
        && literals.some(value => /^(?:\.?xml|[^\\/]+\.xml)$/i.test(value));
}

function xmlTemplateContexts(template) {
    if (!ts.isTemplateExpression(template)) return [];
    const contexts = [];
    let prefix = template.head.text;
    for (const span of template.templateSpans) {
        const context = xmlInterpolationContext(prefix);
        if (context) contexts.push(context);
        prefix += span.literal.text;
    }
    return contexts;
}

function structuredXmlTemplateCandidate(template, node, sourceFile, symbolAt, sink) {
    if (!template) return null;
    const helper = sink === 'xml_mime_helper_call_unverified';
    let evidenceScope = helper
        ? 'direct_template_to_xml_mime_helper_call'
        : 'direct_template_to_literal_xml_sink';
    if (ts.isIdentifier(template)) {
        const declarations = symbolAt(template)?.declarations || [];
        if (declarations.length !== 1 || !ts.isVariableDeclaration(declarations[0])) return null;
        const declaration = declarations[0];
        if (declaration.getSourceFile() !== sourceFile
            || !ts.isIdentifier(declaration.name)
            || !ts.isVariableDeclarationList(declaration.parent)
            || !(declaration.parent.flags & ts.NodeFlags.Const)
            || !ts.isTemplateExpression(declaration.initializer)
            || declaration.getStart(sourceFile) >= node.getStart(sourceFile)) return null;
        template = declaration.initializer;
        evidenceScope = helper
            ? 'same_file_const_template_to_xml_mime_helper_call'
            : 'same_file_const_template_to_literal_xml_sink';
    }
    const contexts = xmlTemplateContexts(template);
    if (!contexts.length) return null;
    return {
        kind: 'xml_template_interpolation_candidate',
        sink,
        interpolation_contexts: [...new Set(contexts)].sort(),
        interpolation_count: contexts.length,
        line: sourceFile.getLineAndCharacterOfPosition(template.getStart(sourceFile)).line + 1,
        evidence_scope: evidenceScope,
        proof_status: 'needs_format_native_round_trip',
    };
}

function structuredXmlSinkCandidate(node, sourceFile, importBindings, symbolAt) {
    let template = null;
    let sink = '';
    if (ts.isNewExpression(node) && ts.isIdentifier(node.expression)
        && node.expression.text === 'Response' && isUnshadowedResponse(node.expression, symbolAt)
        && xmlResponseMime(node.arguments?.[1])) {
        template = node.arguments?.[0];
        sink = 'xml_http_response';
    } else if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
        && ['writeFile', 'writeFileSync'].includes(node.expression.name.text)
        && ts.isStringLiteralLike(node.arguments[0])
        && /\.xml$/i.test(node.arguments[0].text)
        && isNodeFsWriter(node, importBindings, symbolAt)) {
        template = node.arguments[1];
        sink = 'xml_file_write';
    } else if (ts.isCallExpression(node)
        && isImportedXmlMimeHelper(node, importBindings, symbolAt)) {
        // Literal format intent and an imported binding are not proof that the
        // helper writes a file or serializes safely. Keep this advisory only.
        sink = 'xml_mime_helper_call_unverified';
        const mimePositions = node.arguments.flatMap((arg, index) =>
            ts.isStringLiteralLike(arg) && /^(?:application|text)\/xml(?:\s*;|$)/i.test(arg.text)
                ? [index] : []);
        const candidates = node.arguments.flatMap((arg, index) => {
            if (!mimePositions.some(mimeIndex => mimeIndex > index)) return [];
            const candidate = structuredXmlTemplateCandidate(
                arg, node, sourceFile, symbolAt, sink);
            return candidate ? [{index, candidate}] : [];
        });
        if (candidates.length !== 1) return null;
        const {index, candidate} = candidates[0];
        const followsLiteralFormat = mimePositions.some(mimeIndex =>
            mimeIndex > index && node.arguments.slice(index + 1, mimeIndex).every(next =>
                ts.isStringLiteralLike(next)
                || (ts.isTemplateExpression(next) && !xmlTemplateContexts(next).length)));
        return followsLiteralFormat ? candidate : null;
    }
    return structuredXmlTemplateCandidate(template, node, sourceFile, symbolAt, sink);
}

function getModifierNames(node) {
    return (node.modifiers || []).map(m => ts.tokenToString(m.kind)).filter(Boolean);
}

function getHeritageTypes(node, kindName, sourceFile) {
    if (!node.heritageClauses) return [];
    const clause = node.heritageClauses.find(c => c.token === kindName);
    if (!clause) return [];
    return clause.types.map(t => t.getText(sourceFile)).filter(Boolean);
}

function getMemberNames(node, sourceFile) {
    if (!node.members) return [];
    const names = [];
    for (const member of node.members) {
        if (member.name && typeof member.name.getText === 'function') {
            const memberName = member.name.getText(sourceFile);
            if (memberName) names.push(memberName);
        }
    }
    return names;
}

function getMemberKind(member) {
    if (ts.isPropertySignature(member) || ts.isPropertyDeclaration(member)) return 'property';
    if (ts.isMethodSignature(member) || ts.isMethodDeclaration(member)) return 'method';
    if (ts.isConstructSignatureDeclaration(member) || ts.isConstructorDeclaration(member)) return 'constructor';
    if (ts.isGetAccessorDeclaration(member)) return 'getter';
    if (ts.isSetAccessorDeclaration(member)) return 'setter';
    if (ts.isIndexSignatureDeclaration(member)) return 'index';
    return 'member';
}

function collectNodeDependencies(node, sourceFile, importBindings, selfName = '') {
    const dependencies = [];
    const dependencyImports = [];
    const uiDependencies = [];
    const dynamicImports = [];
    const architecturalMarkers = [];
    const sideEffectMarkers = [];
    const sideEffectImports = [];
    const sideEffectCalls = [];

    function pushDependency(value) {
        if (!value || value === selfName || dependencies.includes(value)) return;
        dependencies.push(value);
    }

    function pushImportBinding(binding) {
        if (!binding) return;
        if (!dependencyImports.some(entry => entry.localName === binding.localName && entry.source === binding.source)) {
            dependencyImports.push(binding);
        }
    }

    function pushSideEffectImport(binding) {
        if (!binding) return;
        if (!sideEffectImports.some(entry => entry.localName === binding.localName && entry.source === binding.source)) {
            sideEffectImports.push(binding);
        }
    }

    function pushSideEffectCall(marker, expression, binding, triggerKind = 'access') {
        const payload = {
            marker,
            expression: expression || '',
            localName: binding?.localName || '',
            source: binding?.source || '',
            triggerKind,
        };
        if (!payload.marker || !payload.expression) return;
        if (
            !sideEffectCalls.some(entry =>
                entry.marker === payload.marker &&
                entry.expression === payload.expression &&
                entry.localName === payload.localName &&
                entry.source === payload.source &&
                entry.triggerKind === payload.triggerKind
            )
        ) {
            sideEffectCalls.push(payload);
        }
    }

    function pushUnique(target, value) {
        if (!value || target.includes(value)) return;
        target.push(value);
    }

    function collectSideEffectMarkersFromText(text) {
        const markers = [];
        for (const [marker, names] of Object.entries(SIDE_EFFECT_IDENTIFIERS)) {
            if (names.includes(text)) {
                markers.push(marker);
            }
        }
        for (const [marker, patterns] of SIDE_EFFECT_PROPERTY_PATTERNS) {
            if (patterns.some(pattern => text.includes(pattern))) {
                markers.push(marker);
            }
        }
        return [...new Set(markers)];
    }

    function walk(inner) {
        if (ts.isIdentifier(inner)) {
            const id = inner.getText(sourceFile);
            if (id.length > 2) {
                pushDependency(id);
                pushImportBinding(importBindings.get(id));
                const marker = ARCH_MARKERS.find(m => id.endsWith(m) || id.startsWith(m));
                if (marker) {
                    pushUnique(architecturalMarkers, marker);
                }
                const binding = importBindings.get(id);
                for (const marker of collectSideEffectMarkersFromText(id)) {
                    pushUnique(sideEffectMarkers, marker);
                    pushSideEffectImport(binding);
                    pushSideEffectCall(marker, id, binding, 'access');
                }
            }
        } else if (ts.isPropertyAccessExpression(inner)) {
            const exprText = inner.getText(sourceFile);
            const rootName = ts.isIdentifier(inner.expression)
                ? inner.expression.getText(sourceFile)
                : (ts.isPropertyAccessExpression(inner.expression) ? inner.expression.expression.getText(sourceFile) : '');
            const binding = importBindings.get(rootName);
            for (const marker of collectSideEffectMarkersFromText(exprText)) {
                pushUnique(sideEffectMarkers, marker);
                pushSideEffectImport(binding);
                pushSideEffectCall(marker, exprText, binding, 'access');
            }
        } else if (ts.isJsxOpeningElement(inner) || ts.isJsxSelfClosingElement(inner)) {
            const tagName = inner.tagName.getText(sourceFile);
            pushDependency(`ui:${tagName}`);
            pushUnique(uiDependencies, tagName);
        } else if (ts.isCallExpression(inner)) {
            const expr = inner.expression;
            if (expr.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(expr) && expr.text === 'require')) {
                const arg = inner.arguments[0];
                if (arg && ts.isStringLiteral(arg)) {
                    pushDependency(`dynamic:${arg.text}`);
                    pushUnique(dynamicImports, arg.text);
                }
            }
            const exprText = expr.getText(sourceFile);
            const rootName = ts.isIdentifier(expr)
                ? expr.getText(sourceFile)
                : (ts.isPropertyAccessExpression(expr) && ts.isIdentifier(expr.expression) ? expr.expression.getText(sourceFile) : '');
            const binding = importBindings.get(rootName);
            for (const marker of collectSideEffectMarkersFromText(exprText)) {
                pushUnique(sideEffectMarkers, marker);
                pushSideEffectImport(binding);
                pushSideEffectCall(marker, exprText, binding, 'call');
            }
        }
        ts.forEachChild(inner, walk);
    }

    ts.forEachChild(node, walk);
    return {
        dependencies,
        dependencyImports,
        uiDependencies,
        dynamicImports,
        architecturalMarkers,
        sideEffectMarkers,
        sideEffectImports,
        sideEffectCalls,
    };
}

function createFileSymbolResolver(sourceFile) {
    // One lazy, in-memory binding context per parsed file: no tsconfig, dependency
    // reads, emit, diagnostics run or target execution. This is not type safety.
    let checker;
    return function symbolAt(node) {
        if (!checker) {
            const fileName = path.resolve(sourceFile.fileName);
            const isSource = name => path.resolve(name) === fileName;
            const host = {
                getSourceFile: name => isSource(name) ? sourceFile : undefined,
                getDefaultLibFileName: () => '', writeFile: () => {},
                getCurrentDirectory: () => path.dirname(fileName),
                getCanonicalFileName: name => name.replace(/\\/g, '/'),
                useCaseSensitiveFileNames: () => true, getNewLine: () => '\n',
                fileExists: isSource, readFile: () => undefined,
                directoryExists: () => false, getDirectories: () => [],
            };
            checker = ts.createProgram([sourceFile.fileName], {
                noLib: true, noResolve: true, noEmit: true, allowJs: true,
                target: ts.ScriptTarget.Latest,
            }, host).getTypeChecker();
        }
        return checker.getSymbolAtLocation(node);
    };
}

function createImportCallCollector(sourceFile, importBindings, symbolAt) {
    const cache = new WeakMap();
    function unwrap(node) {
        while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node)
            || ts.isTypeAssertionExpression(node) || ts.isSatisfiesExpression(node)
            || ts.isNonNullExpression(node))) node = node.expression;
        return node;
    }
    return function collectImportCalls(node, scope = 'callable') {
        const moduleRoot = scope === 'module_root' && node === sourceFile;
        const fn = moduleRoot ? sourceFile
            : unwrap(ts.isVariableDeclaration(node) || ts.isPropertyAssignment(node) || ts.isPropertyDeclaration(node)
                ? node.initializer : node);
        const result = {status: 'not_applicable', calls: [], limitations: [],
            binding_scope: 'single_file_lexical_import', runtime_execution: 'not_established'};
        if (moduleRoot) {
            result.callsite_scope = 'module_root';
            result.omitted = 0;
            result.literal_argument_evidence_version = 1;
        }
        function propertyEventEvidence() {
            if (!result.property_event_evidence) {
                result.property_event_evidence = {status: 'not_applicable', calls: [], omitted: 0,
                    limitations: ['imported_value_runtime_identity_unverified'],
                    binding_scope: 'single_file_lexical_named_import_property',
                    runtime_owner_binding: 'not_established', runtime_execution: 'not_established'};
            }
            return result.property_event_evidence;
        }
        if (!fn || (!moduleRoot && (!ts.isFunctionLike(fn) || !fn.body))) return result;
        if (cache.has(fn)) return cache.get(fn);
        cache.set(fn, result);
        if (sourceFile.parseDiagnostics?.length) {
            result.status = 'unavailable';
            result.limitations = ['source_parse_diagnostics'];
            return result;
        }
        const limitations = new Set([moduleRoot
            ? 'module_root_outside_nested_callable_and_class'
            : 'body_only_no_parameter_initializers']);
        const eventMethods = new Set(['emit', 'on', 'once', 'off', 'addListener', 'removeListener']);
        function walk(inner, nestedDepth = 0) {
            if (ts.isFunctionLike(inner) || ts.isClassDeclaration(inner) || ts.isClassExpression(inner)) {
                if (ts.isArrowFunction(inner) || ts.isFunctionExpression(inner)) {
                    limitations.add('nested_callable_event_syntax_only');
                    if (inner.body) walk(inner.body, nestedDepth + 1);
                } else {
                    limitations.add('nested_callable_excluded');
                }
                return;
            }
            if (ts.isCallExpression(inner)) {
                const callee = unwrap(inner.expression);
                const staticMember = ts.isPropertyAccessExpression(callee)
                    ? callee.name.text
                    : ts.isElementAccessExpression(callee)
                        && ts.isStringLiteral(callee.argumentExpression)
                        && /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(callee.argumentExpression.text)
                            ? callee.argumentExpression.text : null;
                const owner = ts.isPropertyAccessExpression(callee)
                    ? unwrap(callee.expression) : null;
                const importedRoot = owner && ts.isPropertyAccessExpression(owner)
                    ? unwrap(owner.expression) : null;
                const propertyBinding = importedRoot && ts.isIdentifier(importedRoot)
                    ? importBindings.get(importedRoot.text) : null;
                const firstEvent = inner.arguments[0];
                if (!moduleRoot && propertyBinding?.kind === 'named' && ts.isPropertyAccessExpression(callee)
                    && ts.isPropertyAccessExpression(owner) && ts.isIdentifier(importedRoot)
                    && eventMethods.has(callee.name.text) && !inner.questionDotToken
                    && !callee.questionDotToken && !owner.questionDotToken
                    && firstEvent && (ts.isStringLiteral(firstEvent)
                        || ts.isNoSubstitutionTemplateLiteral(firstEvent))) {
                    const declarations = symbolAt(importedRoot)?.declarations || [];
                    const specifier = declarations.length === 1 && ts.isImportSpecifier(declarations[0])
                        ? declarations[0] : null;
                    const clause = specifier?.parent?.parent;
                    if (clause && ts.isImportClause(clause) && !clause.isTypeOnly
                        && !specifier.isTypeOnly && ts.isImportDeclaration(clause.parent)
                        && clause.parent.moduleSpecifier.text === propertyBinding.source
                        && specifier.name.text === propertyBinding.localName
                        && (specifier.propertyName?.text || specifier.name.text)
                            === propertyBinding.importedName) {
                        const propertyEvidence = propertyEventEvidence();
                        if (nestedDepth > 8) {
                            propertyEvidence.status = 'incomplete_scan';
                            propertyEvidence.limitations.push('nested_callable_depth_exceeded');
                        } else if (propertyEvidence.calls.length < 64) {
                            const start = inner.getStart(sourceFile);
                            propertyEvidence.calls.push({source: propertyBinding.source,
                                localName: propertyBinding.localName,
                                importedName: propertyBinding.importedName,
                                owner_property: owner.name.text, method: callee.name.text,
                                first_literal_argument: firstEvent.text,
                                nested_callable_depth: nestedDepth,
                                line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
                                end_line: sourceFile.getLineAndCharacterOfPosition(
                                    Math.max(start, inner.end - 1)).line + 1});
                        } else propertyEvidence.omitted += 1;
                    }
                }
                const root = ts.isIdentifier(callee) ? callee
                    : staticMember !== null ? unwrap(callee.expression) : null;
                const binding = root && ts.isIdentifier(root) ? importBindings.get(root.text) : null;
                const literalEventMember = binding && binding.kind === 'named'
                    && ts.isPropertyAccessExpression(callee)
                    && ['emit', 'on', 'once', 'off', 'addListener', 'removeListener'].includes(callee.name.text)
                    && inner.arguments[0] && (ts.isStringLiteral(inner.arguments[0])
                        || ts.isNoSubstitutionTemplateLiteral(inner.arguments[0]));
                if (binding && (!nestedDepth || literalEventMember)
                    && (ts.isIdentifier(callee) || binding.kind === 'namespace'
                    || binding.kind === 'named' && staticMember !== null
                    && (moduleRoot || literalEventMember))) {
                    const declarations = symbolAt(root)?.declarations || [];
                    const declaration = declarations.length === 1 ? declarations[0] : null;
                    const clause = declaration && (ts.isImportClause(declaration) ? declaration
                        : ts.isImportSpecifier(declaration) ? declaration.parent.parent
                        : ts.isNamespaceImport(declaration) ? declaration.parent : null);
                    if (!clause || !ts.isImportClause(clause)) {
                        limitations.add('non_import_or_shadowed_callee');
                    } else if (clause.isTypeOnly || declaration.isTypeOnly) {
                        limitations.add('type_only_import_not_runtime_callee');
                    } else if (!ts.isImportDeclaration(clause.parent)
                        || clause.parent.moduleSpecifier.text !== binding.source
                        || declaration.name?.text !== binding.localName) {
                        limitations.add('import_declaration_identity_mismatch');
                    } else if (moduleRoot && (!['namespace', 'named'].includes(binding.kind)
                        || staticMember === null
                        || inner.questionDotToken || callee.questionDotToken)) {
                        limitations.add('unsupported_module_root_call_form');
                    } else {
                        const start = inner.getStart(sourceFile);
                        const line = sourceFile.getLineAndCharacterOfPosition(start).line + 1;
                        const endLine = sourceFile.getLineAndCharacterOfPosition(Math.max(start, inner.end - 1)).line + 1;
                        const first = inner.arguments[0];
                        if (moduleRoot && result.calls.length >= 64) {
                            result.omitted += 1;
                            limitations.add('module_root_call_cap_exceeded');
                        } else {
                            result.calls.push({...binding, member: staticMember,
                                ...(first && (ts.isStringLiteral(first)
                                    || ts.isNoSubstitutionTemplateLiteral(first))
                                    ? {first_literal_argument: first.text} : {}),
                                ...(binding.kind === 'named' && literalEventMember
                                    ? {nested_callable_depth: nestedDepth} : {}),
                                line, end_line: endLine, optional: !!inner.questionDotToken || !!callee.questionDotToken});
                        }
                    }
                } else {
                    limitations.add('unsupported_or_non_import_callee');
                }
            }
            ts.forEachChild(inner, child => walk(child, nestedDepth));
        }
        try {
            walk(moduleRoot ? sourceFile : fn.body);
            result.status = 'observed';
            if (result.property_event_evidence?.status === 'not_applicable') {
                result.property_event_evidence.status = result.property_event_evidence.omitted
                    ? 'incomplete_scan' : 'observed';
            }
        } catch (_err) {
            // Binding failure must not erase the existing syntax evidence or emit
            // a partially successful lexical claim.
            result.status = 'unavailable';
            result.calls = [];
            limitations.add('single_file_binding_unavailable');
            if (result.property_event_evidence) {
                result.property_event_evidence.status = 'unavailable';
                result.property_event_evidence.calls = [];
                result.property_event_evidence.omitted = 0;
                result.property_event_evidence.limitations.push('single_file_binding_unavailable');
            }
        }
        result.limitations = [...limitations].sort();
        return result;
    };
}

function factorySourceEvidence(decl, sourceFile, importBindings, symbolAt) {
    // Positive source syntax only. A wrapper may skip or replace its factory,
    // and a package import is not resolved to a runtime export here.
    if (!(decl.parent.flags & ts.NodeFlags.Const) || sourceFile.parseDiagnostics?.length) return null;
    function unwrap(node) {
        while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node)
            || ts.isTypeAssertionExpression(node) || ts.isSatisfiesExpression(node)
            || ts.isNonNullExpression(node))) node = node.expression;
        return node;
    }
    const init = unwrap(decl.initializer);
    if (!init || !ts.isCallExpression(init) || init.questionDotToken
        || !ts.isIdentifier(unwrap(init.expression))) return null;
    const callee = unwrap(init.expression);
    function localFactory(identifier) {
        if (!ts.isIdentifier(identifier)) return null;
        const declarations = symbolAt(identifier)?.declarations || [];
        const candidate = declarations.length === 1 ? declarations[0] : null;
        return candidate && ts.isFunctionDeclaration(candidate)
            && candidate.parent === sourceFile && candidate.body
            && !candidate.asteriskToken
            && !candidate.modifiers?.some(mod => mod.kind === ts.SyntaxKind.AsyncKeyword)
            ? candidate : null;
    }
    let factory = localFactory(callee);
    let initializerForm = 'direct_factory_call';
    if (!factory) {
        const argumentsWithFactory = init.arguments.map(arg => localFactory(unwrap(arg))).filter(Boolean);
        if (argumentsWithFactory.length !== 1) return null;
        factory = argumentsWithFactory[0];
        initializerForm = 'factory_passed_as_argument';
    }
    const statements = factory.body.statements;
    const finalReturn = statements[statements.length - 1];
    if (!finalReturn || !ts.isReturnStatement(finalReturn)) return null;
    function hasEarlierReturn(node) {
        if (ts.isFunctionLike(node) || ts.isClassDeclaration(node) || ts.isClassExpression(node)) return false;
        if (ts.isReturnStatement(node)) return true;
        let found = false;
        ts.forEachChild(node, child => { if (!found) found = hasEarlierReturn(child); });
        return found;
    }
    if (statements.slice(0, -1).some(hasEarlierReturn)) return null;
    let constructed = unwrap(finalReturn.expression);
    let returnForm = 'direct_new_return';
    if (ts.isIdentifier(constructed)) {
        const local = (symbolAt(constructed)?.declarations || []);
        const candidate = local.length === 1 ? local[0] : null;
        if (!candidate || !ts.isVariableDeclaration(candidate)
            || !ts.isIdentifier(candidate.name)
            || !ts.isVariableDeclarationList(candidate.parent)
            || !(candidate.parent.flags & ts.NodeFlags.Const)
            || !ts.isVariableStatement(candidate.parent.parent)
            || candidate.parent.parent.parent !== factory.body) return null;
        constructed = unwrap(candidate.initializer);
        returnForm = 'direct_const_new_return';
    }
    if (!constructed || !ts.isNewExpression(constructed)
        || !ts.isIdentifier(unwrap(constructed.expression))) return null;
    const classIdentifier = unwrap(constructed.expression);
    const binding = importBindings.get(classIdentifier.text);
    if (!binding || binding.kind !== 'named') return null;
    const declarations = symbolAt(classIdentifier)?.declarations || [];
    const specifier = declarations.length === 1 && ts.isImportSpecifier(declarations[0])
        ? declarations[0] : null;
    const clause = specifier?.parent?.parent;
    if (!clause || !ts.isImportClause(clause) || clause.isTypeOnly
        || specifier.isTypeOnly || !ts.isImportDeclaration(clause.parent)
        || clause.parent.moduleSpecifier.text !== binding.source
        || specifier.name.text !== binding.localName
        || (specifier.propertyName?.text || specifier.name.text) !== binding.importedName) return null;
    const line = node => sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
    return {status: 'syntax_candidate', binding_scope: 'single_file_lexical_factory_and_named_constructor_import',
        initializer_form: initializerForm, wrapper_name: initializerForm === 'factory_passed_as_argument'
            ? callee.text : null,
        factory: factory.name.text, factory_line: line(factory), return_line: line(finalReturn),
        return_form: returnForm, constructed_imported_name: binding.importedName,
        constructor_module_source: binding.source,
        factory_execution: 'not_established', runtime_instance_identity: 'not_established',
        package_condition_resolution: 'not_evaluated', public_field_mutation: 'not_checked'};
}

function eventEmitterSingletonEvidence(decl, sourceFile, importBindings, symbolAt) {
    const result = {status: 'unavailable', binding_scope: 'single_file_lexical_constructor_import',
        runtime_execution: 'not_established'};
    if (!(decl.parent.flags & ts.NodeFlags.Const)) return null;
    let init = decl.initializer;
    while (init && (ts.isParenthesizedExpression(init) || ts.isAsExpression(init)
        || ts.isSatisfiesExpression(init))) init = init.expression;
    if (!init || !ts.isNewExpression(init) || !ts.isIdentifier(init.expression)) return null;
    const ctor = init.expression;
    const binding = importBindings.get(ctor.text);
    if (!binding || binding.kind !== 'named' || binding.importedName !== 'EventEmitter'
        || !['node:events', 'events'].includes(binding.source)) return null;
    if (sourceFile.parseDiagnostics?.length) return result;
    const declarations = symbolAt(ctor)?.declarations || [];
    if (declarations.length !== 1 || !ts.isImportSpecifier(declarations[0])) return result;
    const specifier = declarations[0];
    const clause = specifier.parent.parent;
    if (!ts.isImportClause(clause) || clause.isTypeOnly || specifier.isTypeOnly
        || !ts.isImportDeclaration(clause.parent)
        || clause.parent.moduleSpecifier.text !== binding.source
        || specifier.name.text !== binding.localName) return result;
    return {...result, status: 'observed', constructor: 'EventEmitter', module_source: binding.source};
}

function classInstanceEventCallEvidence(node, sourceFile, importBindings, symbolAt) {
    const cap = 64;
    const result = {status: 'observed', calls: [], omitted: 0, limitations: [],
        binding_scope: 'single_file_lexical_readonly_class_field_named_import',
        runtime_owner_binding: 'not_established', runtime_execution: 'not_established'};
    if (sourceFile.parseDiagnostics?.length) {
        result.status = 'unavailable';
        result.limitations = ['source_parse_diagnostics'];
        return result;
    }
    const limitations = new Set(['direct_readonly_field_initializer_and_method_body_only']);
    const eventMethods = new Set(['emit', 'on', 'once', 'off', 'addListener', 'removeListener']);
    function unwrap(inner) {
        while (inner && (ts.isParenthesizedExpression(inner) || ts.isAsExpression(inner)
            || ts.isTypeAssertionExpression(inner) || ts.isSatisfiesExpression(inner)
            || ts.isNonNullExpression(inner))) inner = inner.expression;
        return inner;
    }
    const nameCounts = new Map();
    for (const member of node.members) {
        if (member.name && ts.isIdentifier(member.name)) {
            nameCounts.set(member.name.text, (nameCounts.get(member.name.text) || 0) + 1);
        }
    }
    const fields = new Map();
    for (const member of node.members) {
        if (!ts.isPropertyDeclaration(member) || !member.name || !ts.isIdentifier(member.name)
            || nameCounts.get(member.name.text) !== 1 || member.questionToken
            || member.modifiers?.some(mod => [ts.SyntaxKind.StaticKeyword,
                ts.SyntaxKind.DeclareKeyword, ts.SyntaxKind.AbstractKeyword].includes(mod.kind))
            || !member.modifiers?.some(mod => mod.kind === ts.SyntaxKind.ReadonlyKeyword)) continue;
        const initializer = unwrap(member.initializer);
        if (!initializer || !ts.isIdentifier(initializer)) continue;
        const binding = importBindings.get(initializer.text);
        if (!binding || binding.kind !== 'named') continue;
        const declarations = symbolAt(initializer)?.declarations || [];
        const declaration = declarations.length === 1 ? declarations[0] : null;
        const clause = declaration && ts.isImportSpecifier(declaration)
            ? declaration.parent?.parent : null;
        if (!clause || !ts.isImportClause(clause) || clause.isTypeOnly
            || declaration.isTypeOnly || !ts.isImportDeclaration(clause.parent)
            || clause.parent.moduleSpecifier.text !== binding.source
            || declaration.name.text !== binding.localName
            || (declaration.propertyName?.text || declaration.name.text) !== binding.importedName) {
            limitations.add('field_initializer_import_binding_unavailable');
            continue;
        }
        fields.set(member.name.text, {binding, declaration: member});
    }
    if (!fields.size) {
        result.limitations = [...limitations].sort();
        return result;
    }
    const mutated = new Set();
    function fieldName(inner) {
        const access = unwrap(inner);
        return access && ts.isPropertyAccessExpression(access)
            && access.expression.kind === ts.SyntaxKind.ThisKeyword ? access.name.text : null;
    }
    function findMutation(inner) {
        if (ts.isBinaryExpression(inner)
            && inner.operatorToken.kind >= ts.SyntaxKind.FirstAssignment
            && inner.operatorToken.kind <= ts.SyntaxKind.LastAssignment) {
            const name = fieldName(inner.left);
            if (name) mutated.add(name);
        } else if (ts.isDeleteExpression(inner) || ts.isPrefixUnaryExpression(inner)
            || ts.isPostfixUnaryExpression(inner)) {
            const name = fieldName(inner.expression || inner.operand);
            if (name) mutated.add(name);
        }
        ts.forEachChild(inner, findMutation);
    }
    try {
        for (const member of node.members) ts.forEachChild(member, findMutation);
        for (const member of node.members) {
            if (!member.name || !ts.isIdentifier(member.name)
                || member.modifiers?.some(mod => mod.kind === ts.SyntaxKind.StaticKeyword)) continue;
            const fn = ts.isMethodDeclaration(member) ? member
                : ts.isPropertyDeclaration(member) ? unwrap(member.initializer) : null;
            if (!fn || !ts.isFunctionLike(fn) || !fn.body) continue;
            const memberStart = member.getStart(sourceFile);
            const memberLine = sourceFile.getLineAndCharacterOfPosition(memberStart).line + 1;
            const memberEndLine = sourceFile.getLineAndCharacterOfPosition(
                Math.max(memberStart, member.end - 1)).line + 1;
            function walk(inner) {
                if (ts.isFunctionLike(inner) || ts.isClassDeclaration(inner)
                    || ts.isClassExpression(inner)) {
                    limitations.add('nested_callable_excluded');
                    return;
                }
                if (ts.isCallExpression(inner) && !inner.questionDotToken) {
                    const callee = unwrap(inner.expression);
                    const receiver = callee && ts.isPropertyAccessExpression(callee)
                        ? unwrap(callee.expression) : null;
                    const name = fieldName(receiver);
                    const field = name && fields.get(name);
                    const first = inner.arguments[0];
                    if (field && !mutated.has(name) && !callee.questionDotToken
                        && !receiver.questionDotToken && eventMethods.has(callee.name.text)
                        && first && (ts.isStringLiteral(first)
                            || ts.isNoSubstitutionTemplateLiteral(first))) {
                        const declarations = symbolAt(receiver.name)?.declarations || [];
                        if (declarations.length !== 1 || declarations[0] !== field.declaration) {
                            limitations.add('instance_field_binding_unavailable');
                        } else {
                            const start = inner.getStart(sourceFile);
                            const call = {...field.binding, owner_field: name,
                                caller_member: member.name.text, member_line: memberLine,
                                member_end_line: memberEndLine, member: callee.name.text,
                                first_literal_argument: first.text,
                                line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
                                end_line: sourceFile.getLineAndCharacterOfPosition(
                                    Math.max(start, inner.end - 1)).line + 1};
                            if (result.calls.length < cap) result.calls.push(call);
                            else result.omitted += 1;
                        }
                    } else if (field && mutated.has(name)) {
                        limitations.add('class_field_reassignment_excluded');
                    }
                }
                ts.forEachChild(inner, walk);
            }
            walk(fn.body);
        }
        if (result.omitted) result.status = 'incomplete_scan';
    } catch (_err) {
        result.status = 'unavailable';
        result.calls = [];
        result.omitted = 0;
        limitations.add('single_file_instance_field_binding_unavailable');
    }
    result.limitations = [...limitations].sort();
    return result;
}

function classDirectEventEmitterFieldEvidence(node, sourceFile, importBindings, symbolAt) {
    // This records constructor and direct this.field call syntax only. Public
    // property writes outside this file and runtime instance identity are unknown.
    const fields = new Map();
    if (!ts.isClassDeclaration(node)) return fields;
    const cap = 64;
    const eventMethods = new Set(['emit', 'on', 'once', 'off', 'addListener', 'removeListener']);
    function unwrap(inner) {
        while (inner && (ts.isParenthesizedExpression(inner) || ts.isAsExpression(inner)
            || ts.isTypeAssertionExpression(inner) || ts.isSatisfiesExpression(inner)
            || ts.isNonNullExpression(inner))) inner = inner.expression;
        return inner;
    }
    const nameCounts = new Map();
    for (const member of node.members) {
        if (member.name && ts.isIdentifier(member.name)) {
            nameCounts.set(member.name.text, (nameCounts.get(member.name.text) || 0) + 1);
        }
    }
    for (const member of node.members) {
        if (!ts.isPropertyDeclaration(member) || !member.name || !ts.isIdentifier(member.name)
            || nameCounts.get(member.name.text) !== 1 || member.questionToken
            || member.modifiers?.some(mod => [ts.SyntaxKind.StaticKeyword,
                ts.SyntaxKind.DeclareKeyword, ts.SyntaxKind.AbstractKeyword].includes(mod.kind))) continue;
        const init = unwrap(member.initializer);
        if (!init || !ts.isNewExpression(init) || !ts.isIdentifier(init.expression)) continue;
        const ctor = init.expression;
        const binding = importBindings.get(ctor.text);
        if (!binding || binding.kind !== 'named' || binding.importedName !== 'EventEmitter'
            || !['node:events', 'events'].includes(binding.source)) continue;
        const declarations = symbolAt(ctor)?.declarations || [];
        const specifier = declarations.length === 1 && ts.isImportSpecifier(declarations[0])
            ? declarations[0] : null;
        const clause = specifier?.parent?.parent;
        if (!clause || !ts.isImportClause(clause) || clause.isTypeOnly || specifier.isTypeOnly
            || !ts.isImportDeclaration(clause.parent)
            || clause.parent.moduleSpecifier.text !== binding.source
            || specifier.name.text !== binding.localName
            || (specifier.propertyName?.text || specifier.name.text) !== 'EventEmitter') continue;
        fields.set(member.name.text, {
            declaration: member,
            evidence: {status: sourceFile.parseDiagnostics?.length ? 'unavailable' : 'observed',
                constructor: 'EventEmitter', module_source: binding.source,
                binding_scope: 'single_file_lexical_class_field_direct_constructor',
                runtime_owner_binding: 'not_established', runtime_execution: 'not_established',
                calls: [], omitted: 0,
                limitations: ['direct_constructor_and_lexical_arrow_body_only',
                    'external_property_writes_unverified']},
        });
    }
    if (!fields.size || sourceFile.parseDiagnostics?.length) return fields;
    function fieldName(inner) {
        const access = unwrap(inner);
        return access && ts.isPropertyAccessExpression(access)
            && access.expression.kind === ts.SyntaxKind.ThisKeyword ? access.name.text : null;
    }
    const mutated = new Set();
    function findMutation(inner) {
        if (ts.isBinaryExpression(inner)
            && inner.operatorToken.kind >= ts.SyntaxKind.FirstAssignment
            && inner.operatorToken.kind <= ts.SyntaxKind.LastAssignment) {
            const name = fieldName(inner.left);
            if (name) mutated.add(name);
        } else if (ts.isDeleteExpression(inner) || ts.isPrefixUnaryExpression(inner)
            || ts.isPostfixUnaryExpression(inner)) {
            const name = fieldName(inner.expression || inner.operand);
            if (name) mutated.add(name);
        }
        ts.forEachChild(inner, findMutation);
    }
    try {
        for (const member of node.members) ts.forEachChild(member, findMutation);
        for (const member of node.members) {
            if (!member.name || !ts.isIdentifier(member.name)
                || member.modifiers?.some(mod => mod.kind === ts.SyntaxKind.StaticKeyword)) continue;
            const fn = ts.isMethodDeclaration(member) ? member
                : ts.isPropertyDeclaration(member) ? unwrap(member.initializer) : null;
            if (!fn || !ts.isFunctionLike(fn) || !fn.body) continue;
            const memberStart = member.getStart(sourceFile);
            const memberLine = sourceFile.getLineAndCharacterOfPosition(memberStart).line + 1;
            const memberEndLine = sourceFile.getLineAndCharacterOfPosition(
                Math.max(memberStart, member.end - 1)).line + 1;
            function walk(inner, nestedDepth) {
                if (ts.isArrowFunction(inner)) {
                    if (nestedDepth >= 8) {
                        fieldDepthLimit();
                        return;
                    }
                    walk(inner.body, nestedDepth + 1);
                    return;
                }
                if (ts.isFunctionLike(inner) || ts.isClassDeclaration(inner)
                    || ts.isClassExpression(inner)) return;
                if (ts.isCallExpression(inner) && !inner.questionDotToken) {
                    const callee = unwrap(inner.expression);
                    const receiver = callee && ts.isPropertyAccessExpression(callee)
                        ? unwrap(callee.expression) : null;
                    const name = fieldName(receiver);
                    const field = name && fields.get(name);
                    const first = inner.arguments[0];
                    if (field && !mutated.has(name) && !callee.questionDotToken
                        && !receiver.questionDotToken && eventMethods.has(callee.name.text)
                        && first && (ts.isStringLiteral(first)
                            || ts.isNoSubstitutionTemplateLiteral(first))) {
                        const declarations = symbolAt(receiver.name)?.declarations || [];
                        if (declarations.length !== 1 || declarations[0] !== field.declaration) {
                            field.evidence.status = 'unavailable';
                            field.evidence.calls = [];
                            field.evidence.limitations.push('instance_field_binding_unavailable');
                        } else if (field.evidence.status === 'observed') {
                            const start = inner.getStart(sourceFile);
                            const call = {owner_field: name, caller_member: member.name.text,
                                member_line: memberLine, member_end_line: memberEndLine,
                                method: callee.name.text, first_literal_argument: first.text,
                                nested_callable_depth: nestedDepth,
                                line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
                                end_line: sourceFile.getLineAndCharacterOfPosition(
                                    Math.max(start, inner.end - 1)).line + 1};
                            if (field.evidence.calls.length < cap) field.evidence.calls.push(call);
                            else field.evidence.omitted += 1;
                        }
                    }
                }
                ts.forEachChild(inner, child => walk(child, nestedDepth));
            }
            function fieldDepthLimit() {
                for (const field of fields.values()) {
                    field.evidence.limitations.push('nested_arrow_depth_exceeded');
                    if (field.evidence.status === 'observed') {
                        field.evidence.status = 'incomplete_scan';
                    }
                }
            }
            walk(fn.body, 0);
        }
        for (const [name, field] of fields) {
            if (mutated.has(name)) {
                field.evidence.status = 'unavailable';
                field.evidence.calls = [];
                field.evidence.omitted = 0;
                field.evidence.limitations.push('class_field_reassignment_excluded');
            } else if (field.evidence.omitted) field.evidence.status = 'incomplete_scan';
        }
    } catch (_err) {
        for (const field of fields.values()) {
            field.evidence.status = 'unavailable';
            field.evidence.calls = [];
            field.evidence.omitted = 0;
            field.evidence.limitations.push('single_file_instance_field_binding_unavailable');
        }
    }
    return fields;
}

function createSameFileStoreActionCallCollector(sourceFile, importBindings, symbolAt) {
    const cache = new WeakMap();
    const rehydrateCap = 64;
    function unwrap(node) {
        while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node)
            || ts.isTypeAssertionExpression(node) || ts.isSatisfiesExpression(node)
            || ts.isNonNullExpression(node))) node = node.expression;
        return node;
    }
    return function collectSameFileStoreActionCalls(node) {
        const fn = unwrap(ts.isVariableDeclaration(node) ? node.initializer : node);
        const result = {status: 'not_applicable', calls: [], rehydrate_calls: [],
            rehydrate_omitted: 0, imported_rehydrate_calls: [],
            imported_rehydrate_omitted: 0, limitations: [],
            binding_scope: 'single_file_lexical_store_or_named_import', runtime_execution: 'not_established'};
        if (!fn || !ts.isFunctionLike(fn) || !fn.body) return result;
        if (cache.has(fn)) return cache.get(fn);
        cache.set(fn, result);
        if (sourceFile.parseDiagnostics?.length) {
            result.status = 'unavailable';
            result.limitations = ['source_parse_diagnostics'];
            return result;
        }
        const limitations = new Set(['body_only_no_parameter_initializers',
            'direct_or_const_destructured_store_getstate_action_only']);
        function recordCall(store, actionName, call, callForm) {
            const declarations = symbolAt(store)?.declarations || [];
            const declaration = declarations.length === 1 ? declarations[0] : null;
            const statement = declaration && ts.isVariableDeclaration(declaration)
                ? declaration.parent?.parent : null;
            const start = call.getStart(sourceFile);
            const end = Math.max(start, call.end - 1);
            const site = {store: store.text, action: actionName, call_form: callForm,
                line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
                end_line: sourceFile.getLineAndCharacterOfPosition(end).line + 1};
            if (statement && ts.isVariableStatement(statement)
                && statement.parent === sourceFile
                && statement.modifiers?.some(mod => mod.kind === ts.SyntaxKind.ExportKeyword)
                && ts.isIdentifier(declaration.name) && declaration.name.text === store.text) {
                result.calls.push({...site, binding_kind: 'same_file_export'});
            } else if (declaration && ts.isImportSpecifier(declaration)) {
                const clause = declaration.parent?.parent;
                const binding = importBindings.get(store.text);
                if (clause && ts.isImportClause(clause) && !clause.isTypeOnly
                    && !declaration.isTypeOnly && ts.isImportDeclaration(clause.parent)
                    && binding?.kind === 'named' && binding.localName === store.text
                    && binding.importedName === (declaration.propertyName?.text || declaration.name.text)
                    && declaration.name.text === store.text
                    && binding.source === clause.parent.moduleSpecifier.text) {
                    result.calls.push({...site, binding_kind: 'named_import',
                        module_source: binding.source, imported_store: binding.importedName});
                } else {
                    limitations.add('type_only_or_mismatched_import_excluded');
                }
            } else {
                limitations.add('non_exported_or_shadowed_store_excluded');
            }
        }
        function recordRehydrate(store, call, aliasName = null, callContext = null) {
            const declarations = symbolAt(store)?.declarations || [];
            const declaration = declarations.length === 1 ? declarations[0] : null;
            const statement = declaration && ts.isVariableDeclaration(declaration)
                ? declaration.parent?.parent : null;
            const start = call.getStart(sourceFile);
            const end = Math.max(start, call.end - 1);
            const site = {store: store.text,
                line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
                end_line: sourceFile.getLineAndCharacterOfPosition(end).line + 1};
            if (callContext) site.call_context = callContext;
            if (aliasName) {
                site.call_form = 'const_local_alias';
                site.alias_name = aliasName;
            }
            if (statement && ts.isVariableStatement(statement)
                && statement.parent === sourceFile
                && statement.modifiers?.some(mod => mod.kind === ts.SyntaxKind.ExportKeyword)
                && ts.isIdentifier(declaration.name) && declaration.name.text === store.text) {
                const sameFileSite = {...site, store_start: declaration.getStart(sourceFile)};
                if (result.rehydrate_calls.length < rehydrateCap) result.rehydrate_calls.push(sameFileSite);
                else result.rehydrate_omitted += 1;
                return;
            }
            if (declaration && ts.isImportSpecifier(declaration)) {
                const clause = declaration.parent?.parent;
                const binding = importBindings.get(store.text);
                if (clause && ts.isImportClause(clause) && !clause.isTypeOnly
                    && !declaration.isTypeOnly && ts.isImportDeclaration(clause.parent)
                    && binding?.kind === 'named' && binding.localName === store.text
                    && binding.importedName === (declaration.propertyName?.text || declaration.name.text)
                    && declaration.name.text === store.text
                    && binding.source === clause.parent.moduleSpecifier.text) {
                    const importedSite = {...site, module_source: binding.source,
                        imported_store: binding.importedName};
                    if (result.imported_rehydrate_calls.length < rehydrateCap)
                        result.imported_rehydrate_calls.push(importedSite);
                    else result.imported_rehydrate_omitted += 1;
                    return;
                }
            }
            limitations.add('non_exported_or_shadowed_rehydrate_store_excluded');
        }
        function walk(inner, callContext = null) {
            if (ts.isFunctionLike(inner) || ts.isClassDeclaration(inner) || ts.isClassExpression(inner)) {
                if (!callContext && (ts.isArrowFunction(inner) || ts.isFunctionExpression(inner))
                    && ts.isCallExpression(inner.parent)
                    && inner.parent.arguments.some(argument => argument === inner) && inner.body) {
                    walk(inner.body, 'inline_callback');
                } else {
                    limitations.add('non_inline_or_deeper_nested_callable_excluded');
                }
                return;
            }
            if (ts.isCallExpression(inner)) {
                const action = unwrap(inner.expression);
                const persist = action && ts.isPropertyAccessExpression(action)
                    ? unwrap(action.expression) : null;
                const rehydrateStore = persist && ts.isPropertyAccessExpression(persist)
                    ? unwrap(persist.expression) : null;
                if (action && ts.isPropertyAccessExpression(action)
                    && action.name.text === 'rehydrate'
                    && persist && ts.isPropertyAccessExpression(persist)
                    && persist.name.text === 'persist'
                    && rehydrateStore && ts.isIdentifier(rehydrateStore)
                    && inner.arguments.length === 0 && !inner.questionDotToken
                    && !action.questionDotToken && !persist.questionDotToken) {
                    recordRehydrate(rehydrateStore, inner, null, callContext);
                }
                if (!callContext && action && ts.isIdentifier(action) && inner.arguments.length === 0
                    && !inner.questionDotToken) {
                    const aliasDeclarations = symbolAt(action)?.declarations || [];
                    const alias = aliasDeclarations.length === 1
                        && ts.isVariableDeclaration(aliasDeclarations[0])
                        ? aliasDeclarations[0] : null;
                    const aliasList = alias?.parent;
                    const aliasStatement = aliasList?.parent;
                    const aliasValue = alias && unwrap(alias.initializer);
                    const aliasPersist = aliasValue && ts.isPropertyAccessExpression(aliasValue)
                        ? unwrap(aliasValue.expression) : null;
                    const aliasStore = aliasPersist && ts.isPropertyAccessExpression(aliasPersist)
                        ? unwrap(aliasPersist.expression) : null;
                    if (alias && ts.isIdentifier(alias.name)
                        && alias.name.text === action.text
                        && aliasList && ts.isVariableDeclarationList(aliasList)
                        && (aliasList.flags & ts.NodeFlags.Const)
                        && aliasStatement && ts.isVariableStatement(aliasStatement)
                        && aliasStatement.parent === fn.body
                        && alias.getStart(sourceFile) < inner.getStart(sourceFile)
                        && aliasValue && ts.isPropertyAccessExpression(aliasValue)
                        && aliasValue.name.text === 'rehydrate' && !aliasValue.questionDotToken
                        && aliasPersist && ts.isPropertyAccessExpression(aliasPersist)
                        && aliasPersist.name.text === 'persist' && !aliasPersist.questionDotToken
                        && aliasStore && ts.isIdentifier(aliasStore)) {
                        recordRehydrate(aliasStore, inner, action.text);
                    }
                }
                const getStateCall = action && ts.isPropertyAccessExpression(action)
                    ? unwrap(action.expression) : null;
                const getState = getStateCall && ts.isCallExpression(getStateCall)
                    ? unwrap(getStateCall.expression) : null;
                const store = getState && ts.isPropertyAccessExpression(getState)
                    ? unwrap(getState.expression) : null;
                if (!callContext && store && ts.isIdentifier(store) && getState.name.text === 'getState'
                    && getStateCall.arguments.length === 0 && !inner.questionDotToken
                    && !action.questionDotToken && !getStateCall.questionDotToken
                    && !getState.questionDotToken) {
                    recordCall(store, action.name.text, inner, 'direct_getstate_action');
                } else if (!callContext && ts.isIdentifier(action) && !inner.questionDotToken) {
                    const declarations = symbolAt(action)?.declarations || [];
                    const binding = declarations.length === 1 && ts.isBindingElement(declarations[0])
                        ? declarations[0] : null;
                    const pattern = binding?.parent;
                    const variable = pattern && ts.isObjectBindingPattern(pattern) ? pattern.parent : null;
                    const list = variable && ts.isVariableDeclaration(variable) ? variable.parent : null;
                    const init = variable && ts.isVariableDeclaration(variable)
                        ? unwrap(variable.initializer) : null;
                    const stateAccess = init && ts.isCallExpression(init) ? unwrap(init.expression) : null;
                    const root = stateAccess && ts.isPropertyAccessExpression(stateAccess)
                        ? unwrap(stateAccess.expression) : null;
                    if (binding && ts.isIdentifier(binding.name) && binding.name.text === action.text
                        && !binding.propertyName && !binding.dotDotDotToken && !binding.initializer
                        && list && ts.isVariableDeclarationList(list) && (list.flags & ts.NodeFlags.Const)
                        && root && ts.isIdentifier(root) && stateAccess.name.text === 'getState'
                        && init.arguments.length === 0 && !init.questionDotToken
                        && !stateAccess.questionDotToken) {
                        recordCall(root, action.text, inner, 'const_destructured_getstate_action');
                    }
                }
            }
            ts.forEachChild(inner, child => walk(child, callContext));
        }
        try {
            walk(fn.body);
            result.status = 'observed';
        } catch (_err) {
            result.status = 'unavailable';
            result.calls = [];
            result.rehydrate_calls = [];
            result.rehydrate_omitted = 0;
            result.imported_rehydrate_calls = [];
            result.imported_rehydrate_omitted = 0;
            limitations.add('single_file_binding_unavailable');
        }
        result.limitations = [...limitations].sort();
        return result;
    };
}

function createSameFileDirectCallCollector(sourceFile, symbolAt) {
    const cache = new WeakMap();
    const cap = 64;
    function unwrap(node) {
        while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node)
            || ts.isTypeAssertionExpression(node) || ts.isSatisfiesExpression(node)
            || ts.isNonNullExpression(node))) node = node.expression;
        return node;
    }
    const directNames = new Set();
    for (const statement of sourceFile.statements) {
        if (ts.isFunctionDeclaration(statement) && statement.name && statement.body) {
            directNames.add(statement.name.text);
        } else if (ts.isVariableStatement(statement)) {
            for (const declaration of statement.declarationList.declarations) {
                if (ts.isIdentifier(declaration.name) && declaration.initializer
                    && ts.isFunctionLike(unwrap(declaration.initializer))) {
                    directNames.add(declaration.name.text);
                }
            }
        }
    }
    return function collectSameFileDirectCalls(node) {
        const fn = unwrap(ts.isVariableDeclaration(node) ? node.initializer : node);
        const result = {status: 'not_applicable', calls: [], omitted: 0, limitations: [],
            binding_scope: 'single_file_lexical_direct_callable', runtime_execution: 'not_established'};
        if (!fn || !ts.isFunctionLike(fn) || !fn.body) return result;
        if (cache.has(fn)) return cache.get(fn);
        cache.set(fn, result);
        if (sourceFile.parseDiagnostics?.length) {
            result.status = 'unavailable';
            result.limitations = ['source_parse_diagnostics'];
            return result;
        }
        const limitations = new Set(['body_only_no_parameter_initializers']);
        function walk(inner) {
            if (ts.isFunctionLike(inner) || ts.isClassDeclaration(inner) || ts.isClassExpression(inner)) {
                limitations.add('nested_callable_excluded');
                return;
            }
            if (ts.isCallExpression(inner) && !inner.questionDotToken) {
                const callee = unwrap(inner.expression);
                if (callee && ts.isIdentifier(callee) && directNames.has(callee.text)) {
                    const declarations = symbolAt(callee)?.declarations || [];
                    const declaration = declarations.length === 1 ? declarations[0] : null;
                    const directFunction = declaration && ts.isFunctionDeclaration(declaration)
                        && declaration.parent === sourceFile && declaration.name?.text === callee.text
                        && !declaration.modifiers?.some(mod => mod.kind === ts.SyntaxKind.DeclareKeyword);
                    const statement = declaration && ts.isVariableDeclaration(declaration)
                        ? declaration.parent?.parent : null;
                    const directVariable = statement && ts.isVariableStatement(statement)
                        && statement.parent === sourceFile && ts.isIdentifier(declaration.name)
                        && declaration.name.text === callee.text
                        && !!declaration.initializer
                        && ts.isFunctionLike(unwrap(declaration.initializer))
                        && !statement.modifiers?.some(mod => mod.kind === ts.SyntaxKind.DeclareKeyword);
                    if (directFunction || directVariable) {
                        const start = inner.getStart(sourceFile);
                        const call = {target_symbol: callee.text,
                            target_start: declaration.getStart(sourceFile),
                            line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
                            end_line: sourceFile.getLineAndCharacterOfPosition(Math.max(start, inner.end - 1)).line + 1};
                        if (result.calls.length < cap) result.calls.push(call);
                        else result.omitted += 1;
                    } else {
                        limitations.add('non_direct_or_shadowed_callee_excluded');
                    }
                }
            }
            ts.forEachChild(inner, walk);
        }
        try {
            walk(fn.body);
            result.status = result.omitted ? 'incomplete_scan' : 'observed';
        } catch (_err) {
            result.status = 'unavailable';
            result.calls = [];
            result.omitted = 0;
            limitations.add('single_file_binding_unavailable');
        }
        result.limitations = [...limitations].sort();
        return result;
    };
}

function createStoreHookSelectorCollector(sourceFile, importBindings, symbolAt) {
    const cache = new WeakMap();
    function unwrap(node) {
        while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node)
            || ts.isTypeAssertionExpression(node) || ts.isSatisfiesExpression(node)
            || ts.isNonNullExpression(node))) node = node.expression;
        return node;
    }
    return function collectStoreHookSelectors(node) {
        const fn = unwrap(ts.isVariableDeclaration(node) ? node.initializer : node);
        const result = {status: 'not_applicable', calls: [], limitations: [],
            binding_scope: 'single_file_lexical_store_or_named_import',
            runtime_execution: 'not_established', subscription: 'not_established'};
        if (!fn || !ts.isFunctionLike(fn) || !fn.body) return result;
        if (cache.has(fn)) return cache.get(fn);
        cache.set(fn, result);
        if (sourceFile.parseDiagnostics?.length) {
            result.status = 'unavailable';
            result.limitations = ['source_parse_diagnostics'];
            return result;
        }
        const limitations = new Set(['body_only_no_parameter_initializers',
            'direct_arrow_property_selector_only',
            'selected_result_direct_const_call_syntax_only',
            'direct_hook_return_only_no_indirect_or_conditional_return']);
        const selectedBindings = new Map();
        function directReturnForm(call) {
            if (fn.asteriskToken || fn.modifiers?.some(mod => mod.kind === ts.SyntaxKind.AsyncKeyword))
                return null;
            let outer = call;
            while (outer.parent && (ts.isParenthesizedExpression(outer.parent)
                || ts.isAsExpression(outer.parent) || ts.isTypeAssertionExpression(outer.parent)
                || ts.isSatisfiesExpression(outer.parent) || ts.isNonNullExpression(outer.parent))
                && outer.parent.expression === outer) outer = outer.parent;
            if (ts.isReturnStatement(outer.parent) && outer.parent.expression === outer
                && outer.parent.parent === fn.body)
                return 'return_statement';
            if (ts.isArrowFunction(fn) && fn.body === outer)
                return 'arrow_expression';
            return null;
        }
        function recordSelector(inner, site, binding) {
            const call = {...site, ...binding};
            const returnForm = directReturnForm(inner);
            if (returnForm) call.direct_return_form = returnForm;
            result.calls.push(call);
            const declaration = inner.parent;
            if (!ts.isVariableDeclaration(declaration) || declaration.initializer !== inner
                || !ts.isIdentifier(declaration.name)
                || !(declaration.parent?.flags & ts.NodeFlags.Const)) return;
            const symbol = symbolAt(declaration.name);
            if (symbol?.declarations?.length === 1 && symbol.declarations[0] === declaration) {
                selectedBindings.set(symbol, {call, declaration});
            }
        }
        function walk(inner) {
            if (ts.isFunctionLike(inner) || ts.isClassDeclaration(inner) || ts.isClassExpression(inner)) {
                limitations.add('nested_callable_excluded');
                return;
            }
            if (ts.isCallExpression(inner)) {
                const store = unwrap(inner.expression);
                const selector = inner.arguments.length === 1 ? unwrap(inner.arguments[0]) : null;
                const body = selector && ts.isArrowFunction(selector) ? unwrap(selector.body) : null;
                const parameter = selector && ts.isArrowFunction(selector) && selector.parameters.length === 1
                    ? selector.parameters[0] : null;
                const selectorShape = store && ts.isIdentifier(store) && !inner.questionDotToken
                    && parameter && ts.isIdentifier(parameter.name) && !parameter.dotDotDotToken
                    && !parameter.initializer && body && ts.isPropertyAccessExpression(body)
                    && !body.questionDotToken && ts.isIdentifier(body.expression);
                const selectorSymbol = selectorShape ? symbolAt(parameter.name) : null;
                if (selectorSymbol && symbolAt(body.expression) === selectorSymbol) {
                    const declarations = symbolAt(store)?.declarations || [];
                    const declaration = declarations.length === 1 ? declarations[0] : null;
                    const statement = declaration && ts.isVariableDeclaration(declaration)
                        ? declaration.parent?.parent : null;
                    const start = inner.getStart(sourceFile);
                    const end = Math.max(start, inner.end - 1);
                    const site = {store: store.text, selected_member: body.name.text,
                        selector_form: 'direct_arrow_property',
                        line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
                        end_line: sourceFile.getLineAndCharacterOfPosition(end).line + 1};
                    if (statement && ts.isVariableStatement(statement)
                        && statement.parent === sourceFile
                        && statement.modifiers?.some(mod => mod.kind === ts.SyntaxKind.ExportKeyword)
                        && ts.isIdentifier(declaration.name) && declaration.name.text === store.text) {
                        recordSelector(inner, site, {binding_kind: 'same_file_export'});
                        return;
                    }
                    const clause = declaration && ts.isImportSpecifier(declaration)
                        ? declaration.parent?.parent : null;
                    const binding = importBindings.get(store.text);
                    if (clause && ts.isImportClause(clause) && !clause.isTypeOnly
                        && !declaration.isTypeOnly && ts.isImportDeclaration(clause.parent)
                        && binding?.kind === 'named' && binding.localName === store.text
                        && binding.importedName === (declaration.propertyName?.text || declaration.name.text)
                        && binding.source === clause.parent.moduleSpecifier.text) {
                        recordSelector(inner, site, {binding_kind: 'named_import',
                            module_source: binding.source, imported_store: binding.importedName});
                        return;
                    }
                    limitations.add('non_exported_shadowed_or_type_only_store_excluded');
                }
            }
            ts.forEachChild(inner, walk);
        }
        function walkResultCalls(inner) {
            if (ts.isFunctionLike(inner) || ts.isClassDeclaration(inner) || ts.isClassExpression(inner)) return;
            if (ts.isCallExpression(inner) && !inner.questionDotToken
                && ts.isIdentifier(inner.expression)) {
                const selected = selectedBindings.get(symbolAt(inner.expression));
                if (selected && inner.getStart(sourceFile) > selected.declaration.end) {
                    const calls = selected.call.selected_result_direct_calls ||= [];
                    if (calls.length < 8) {
                        const start = inner.getStart(sourceFile);
                        const end = Math.max(start, inner.end - 1);
                        calls.push({line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
                            end_line: sourceFile.getLineAndCharacterOfPosition(end).line + 1});
                    } else {
                        selected.call.selected_result_direct_calls_omitted =
                            (selected.call.selected_result_direct_calls_omitted || 0) + 1;
                    }
                }
            }
            ts.forEachChild(inner, walkResultCalls);
        }
        try {
            walk(fn.body);
            if (selectedBindings.size) walkResultCalls(fn.body);
            result.status = 'observed';
        } catch (_err) {
            result.status = 'unavailable';
            result.calls = [];
            limitations.add('single_file_binding_unavailable');
        }
        result.limitations = [...limitations].sort();
        return result;
    };
}

function getMemberDetails(node, sourceFile, importBindings, collectImportCalls, symbolAt) {
    if (!node.members) return [];
    const details = [];
    const eventFields = classDirectEventEmitterFieldEvidence(
        node, sourceFile, importBindings, symbolAt);
    for (const member of node.members) {
        if (!member.name || typeof member.name.getText !== 'function') continue;
        const name = member.name.getText(sourceFile);
        if (!name) continue;
        const usage = collectNodeDependencies(member, sourceFile, importBindings, name);
        details.push({
            name,
            kind: getMemberKind(member),
            line: sourceFile.getLineAndCharacterOfPosition(member.getStart(sourceFile)).line + 1,
            end_line: sourceFile.getLineAndCharacterOfPosition(Math.max(member.getStart(sourceFile), member.end - 1)).line + 1,
            signature: extractSignature(member, sourceFile),
            optional: !!member.questionToken,
            readonly: !!member.modifiers?.some(m => m.kind === ts.SyntaxKind.ReadonlyKeyword),
            static: !!member.modifiers?.some(m => m.kind === ts.SyntaxKind.StaticKeyword),
            dependencies: usage.dependencies,
            dependencyImports: usage.dependencyImports,
            uiDependencies: usage.uiDependencies,
            dynamicImports: usage.dynamicImports,
            architecturalMarkers: usage.architecturalMarkers,
            sideEffectMarkers: usage.sideEffectMarkers,
            sideEffectImports: usage.sideEffectImports,
            sideEffectCalls: usage.sideEffectCalls,
            import_call_evidence: collectImportCalls(member),
            ...(eventFields.has(name)
                ? {class_event_emitter_field_evidence: eventFields.get(name).evidence} : {}),
        });
    }
    return details;
}

function collectInitializerMemberEvidence(initializer, sourceFile, importBindings, collectImportCalls, symbolAt) {
    // Syntax candidates are not class members or proven properties of a call result.
    // Keep them separate so Genome/quality consumers cannot inherit that authority.
    const members = [];
    const limitations = new Set();
    function unwrap(node) {
        while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node)
            || ts.isTypeAssertionExpression(node) || ts.isSatisfiesExpression(node))) {
            node = node.expression;
        }
        return node;
    }
    function isBoundImportCall(call, source, importedName) {
        if (!ts.isCallExpression(call) || call.questionDotToken) return false;
        const callee = unwrap(call.expression);
        const qualified = callee && ts.isPropertyAccessExpression(callee)
            && !callee.questionDotToken && ts.isIdentifier(callee.expression)
            && callee.name.text === importedName;
        const local = callee && ts.isIdentifier(callee) ? callee
            : qualified ? callee.expression : null;
        if (!local) return false;
        const binding = importBindings.get(local.text);
        if (!binding || binding.source !== source
            || (qualified ? binding.kind !== 'namespace'
                : binding.kind !== 'named' || binding.importedName !== importedName)) return false;
        const declarations = symbolAt(local)?.declarations || [];
        const declaration = declarations.length === 1 ? declarations[0] : null;
        const clause = declaration && ts.isImportSpecifier(declaration)
            ? declaration.parent.parent
            : declaration && ts.isNamespaceImport(declaration) ? declaration.parent : null;
        return !!(clause && ts.isImportClause(clause) && !clause.isTypeOnly && !declaration.isTypeOnly
            && ts.isImportDeclaration(clause.parent)
            && clause.parent.moduleSpecifier.text === binding.source
            && declaration.name.text === binding.localName
            && (qualified ? ts.isNamespaceImport(declaration) : ts.isImportSpecifier(declaration)));
    }
    function directZustandSetter(call) {
        if (sourceFile.parseDiagnostics?.length || call.arguments.length !== 1) return null;
        const inner = unwrap(call.expression);
        const curried = inner && ts.isCallExpression(inner);
        const factory = curried ? inner : call;
        if (curried && factory.arguments.length !== 0) return null;
        const reactBoundHook = isBoundImportCall(factory, 'zustand', 'create');
        if (!reactBoundHook && !isBoundImportCall(factory, 'zustand/vanilla', 'createStore')) return null;
        let callback = unwrap(call.arguments[0]);
        let middlewareForm = 'none';
        let persistOptions = null;
        if (callback && ts.isCallExpression(callback)) {
            if (callback.arguments.length !== 2
                || !isBoundImportCall(callback, 'zustand/middleware', 'persist')) return null;
            middlewareForm = 'persist';
            persistOptions = callback.arguments[1];
            callback = unwrap(callback.arguments[0]);
        }
        if (!callback || !ts.isFunctionLike(callback) || !callback.body
            || !callback.parameters?.[0] || !ts.isIdentifier(callback.parameters[0].name)) return null;
        const parameter = callback.parameters[0].name;
        const symbol = symbolAt(parameter);
        let storageOptionEvidence = null;
        let hydrationOptionEvidence = null;
        if (symbol && persistOptions) {
            storageOptionEvidence = collectPersistStorageOptionImport(persistOptions);
            hydrationOptionEvidence = collectPersistHydrationOption(persistOptions);
        }
        return symbol ? {callback, parameter: parameter.text, symbol,
            factory_form: curried ? 'curried' : 'direct', middleware_form: middlewareForm,
            factory_api: reactBoundHook ? 'react_bound_hook' : 'vanilla_store',
            ...(storageOptionEvidence
                ? {persist_storage_option_import_evidence: storageOptionEvidence} : {}),
            ...(hydrationOptionEvidence
                ? {persist_hydration_option_evidence: hydrationOptionEvidence} : {})} : null;
    }
    function collectPersistStorageOptionImport(rawOptions) {
        const unavailable = reason => ({status: 'unavailable', reason,
            runtime_execution: 'not_established'});
        const options = unwrap(rawOptions);
        if (!options || !ts.isObjectLiteralExpression(options)) return unavailable('options_not_object_literal');
        if (options.properties.some(property => ts.isSpreadAssignment(property)
            || (property.name && ts.isComputedPropertyName(property.name)))) {
            return unavailable('spread_or_computed_option_not_resolved');
        }
        const storage = options.properties.filter(property => property.name
            && (ts.isIdentifier(property.name) || ts.isStringLiteral(property.name))
            && property.name.text === 'storage');
        if (storage.length !== 1) return unavailable(storage.length ? 'duplicate_storage_option' : 'storage_option_absent');
        const property = storage[0];
        if (!ts.isPropertyAssignment(property) && !ts.isShorthandPropertyAssignment(property)) {
            return unavailable('storage_option_not_identifier');
        }
        const value = ts.isPropertyAssignment(property) ? unwrap(property.initializer) : property.name;
        if (!value || !ts.isIdentifier(value)) return unavailable('storage_option_not_identifier');
        const binding = importBindings.get(value.text);
        if (!binding || binding.kind !== 'named') return unavailable('storage_identifier_not_named_import');
        const declarations = symbolAt(value)?.declarations || [];
        const declaration = declarations.length === 1 ? declarations[0] : null;
        const clause = declaration && ts.isImportSpecifier(declaration) ? declaration.parent.parent : null;
        if (!(clause && ts.isImportClause(clause) && !clause.isTypeOnly && !declaration.isTypeOnly
            && ts.isImportDeclaration(clause.parent)
            && clause.parent.moduleSpecifier.text === binding.source
            && declaration.name.text === binding.localName)) {
            return unavailable('storage_import_binding_unverified');
        }
        const start = property.getStart(sourceFile);
        const end = Math.max(start, property.end - 1);
        return {status: 'observed', relation: 'persist_storage_option_named_import_syntax',
            reference: {source: binding.source, localName: binding.localName,
                importedName: binding.importedName, kind: binding.kind},
            line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
            end_line: sourceFile.getLineAndCharacterOfPosition(end).line + 1,
            runtime_execution: 'not_established'};
    }
    function collectPersistHydrationOption(rawOptions) {
        const unavailable = reason => ({status: 'unavailable', reason,
            runtime_execution: 'not_established'});
        const options = unwrap(rawOptions);
        if (!options || !ts.isObjectLiteralExpression(options)) return unavailable('options_not_object_literal');
        if (options.properties.some(property => ts.isSpreadAssignment(property)
            || (property.name && ts.isComputedPropertyName(property.name)))) {
            return unavailable('spread_or_computed_option_not_resolved');
        }
        const hydration = options.properties.filter(property => property.name
            && (ts.isIdentifier(property.name) || ts.isStringLiteral(property.name))
            && property.name.text === 'skipHydration');
        if (hydration.length !== 1) {
            return unavailable(hydration.length ? 'duplicate_skip_hydration_option' : 'skip_hydration_option_absent');
        }
        const property = hydration[0];
        if (!ts.isPropertyAssignment(property)) return unavailable('skip_hydration_value_not_literal');
        const value = unwrap(property.initializer);
        if (!value || (value.kind !== ts.SyntaxKind.TrueKeyword
            && value.kind !== ts.SyntaxKind.FalseKeyword)) {
            return unavailable('skip_hydration_value_not_literal');
        }
        const start = property.getStart(sourceFile);
        const end = Math.max(start, property.end - 1);
        return {status: 'observed', relation: 'persist_skip_hydration_literal_syntax',
            skip_hydration: value.kind === ts.SyntaxKind.TrueKeyword,
            line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
            end_line: sourceFile.getLineAndCharacterOfPosition(end).line + 1,
            runtime_execution: 'not_established'};
    }
    function collectSetterCalls(property, setter) {
        const result = {status: 'not_applicable', calls: [], limitations: [],
            factory_form: setter.factory_form, middleware_form: setter.middleware_form,
            factory_api: setter.factory_api,
            binding_scope: 'single_file_lexical_store_factory_parameter', runtime_execution: 'not_established'};
        const fn = ts.isMethodDeclaration(property) ? property
            : ts.isPropertyAssignment(property) ? unwrap(property.initializer) : null;
        if (!fn || !ts.isFunctionLike(fn) || !fn.body) return result;
        if (sourceFile.parseDiagnostics?.length) {
            result.status = 'unavailable';
            result.limitations = ['source_parse_diagnostics'];
            return result;
        }
        const limitations = new Set(['body_only_no_parameter_initializers']);
        function literalKeyEvidence(call) {
            const unavailable = reason => ({status: 'unavailable', keys: [], reason});
            if (!call.arguments.length || ts.isSpreadElement(call.arguments[0])) {
                return unavailable('missing_or_spread_argument');
            }
            const argument = unwrap(call.arguments[0]);
            if (argument && ts.isFunctionLike(argument)) return unavailable('updater_callback_not_resolved');
            if (!argument || !ts.isObjectLiteralExpression(argument)) {
                return unavailable('argument_not_object_literal');
            }
            const keys = [];
            for (const member of argument.properties) {
                if (ts.isSpreadAssignment(member) || member.name && ts.isComputedPropertyName(member.name)) {
                    return unavailable('spread_or_computed_key_not_resolved');
                }
                if (!ts.isPropertyAssignment(member) || !member.name
                    || !(ts.isIdentifier(member.name) || ts.isStringLiteral(member.name))) {
                    return unavailable('unsupported_object_member_not_resolved');
                }
                if (member.name.text === '__proto__') return unavailable('special_object_key_not_resolved');
                const start = member.name.getStart(sourceFile);
                const end = Math.max(start, member.name.end - 1);
                keys.push({name: member.name.text,
                    line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
                    end_line: sourceFile.getLineAndCharacterOfPosition(end).line + 1});
            }
            return {status: 'observed', keys};
        }
        function walk(node) {
            if (ts.isFunctionLike(node) || ts.isClassDeclaration(node) || ts.isClassExpression(node)) {
                limitations.add('nested_callable_excluded');
                return;
            }
            if (ts.isCallExpression(node)) {
                const callee = unwrap(node.expression);
                if (callee && ts.isIdentifier(callee) && callee.text === setter.parameter) {
                    if (symbolAt(callee) === setter.symbol) {
                        const start = node.getStart(sourceFile);
                        const end = Math.max(start, node.end - 1);
                        result.calls.push({parameter: setter.parameter,
                            line: sourceFile.getLineAndCharacterOfPosition(start).line + 1,
                            end_line: sourceFile.getLineAndCharacterOfPosition(end).line + 1,
                            optional: !!node.questionDotToken,
                            literal_key_evidence: literalKeyEvidence(node)});
                    } else {
                        limitations.add('shadowed_setter_call_excluded');
                    }
                } else if (callee && ts.isPropertyAccessExpression(callee)
                    && ts.isIdentifier(callee.expression) && callee.expression.text === setter.parameter) {
                    limitations.add('indirect_setter_call_unresolved');
                }
            }
            ts.forEachChild(node, walk);
        }
        try {
            walk(fn.body);
            result.status = 'observed';
        } catch (_err) {
            result.status = 'unavailable';
            result.calls = [];
            limitations.add('single_file_binding_unavailable');
        }
        result.limitations = [...limitations].sort();
        return result;
    }
    function collectObject(node, attribution, setter = null) {
        for (const property of node.properties) {
            const nameNode = property.name;
            if (!nameNode || !(ts.isIdentifier(nameNode) || ts.isStringLiteral(nameNode))) {
                limitations.add('spread_or_computed_member_not_resolved');
                continue;
            }
            const name = nameNode.text;
            const start = property.getStart(sourceFile);
            const line = sourceFile.getLineAndCharacterOfPosition(start).line + 1;
            const endLine = sourceFile.getLineAndCharacterOfPosition(Math.max(start, property.end - 1)).line + 1;
            const usage = collectNodeDependencies(property, sourceFile, importBindings, name);
            members.push({
                name, kind: getMemberKind(property), attribution,
                line, end_line: endLine, source_lines: `L${line}-L${endLine}`,
                dependencies: usage.dependencies, dependencyImports: usage.dependencyImports,
                import_call_evidence: collectImportCalls(property),
                ...(setter ? {zustand_setter_call_evidence: collectSetterCalls(property, setter)} : {}),
                ...(setter?.persist_storage_option_import_evidence
                    ? {persist_storage_option_import_evidence: setter.persist_storage_option_import_evidence} : {}),
                ...(setter?.persist_hydration_option_evidence
                    ? {persist_hydration_option_evidence: setter.persist_hydration_option_evidence} : {}),
            });
        }
    }
    function collectReturns(fn, setter = null) {
        const body = unwrap(fn.body);
        if (!body) return;
        if (ts.isObjectLiteralExpression(body)) {
            collectObject(body, 'returned_object_candidate', setter);
            return;
        }
        if (!ts.isBlock(body)) {
            limitations.add('return_value_not_object_literal');
            return;
        }
        function visitReturn(node) {
            if (ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node)
                || ts.isArrowFunction(node) || ts.isMethodDeclaration(node)
                || ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node)
                || ts.isClassDeclaration(node) || ts.isClassExpression(node)) return;
            if (ts.isReturnStatement(node)) {
                const value = unwrap(node.expression);
                if (value && ts.isObjectLiteralExpression(value)) collectObject(value, 'returned_object_candidate', setter);
                else limitations.add('return_value_not_object_literal');
                return;
            }
            ts.forEachChild(node, visitReturn);
        }
        ts.forEachChild(body, visitReturn);
    }
    function visitInitializer(raw, direct = false) {
        const node = unwrap(raw);
        if (!node) return;
        if (direct && ts.isObjectLiteralExpression(node)) {
            collectObject(node, 'direct_object_initializer');
        } else if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
            limitations.add('return_to_owner_identity_unverified');
            collectReturns(node);
        } else if (ts.isCallExpression(node)) {
            limitations.add('call_result_identity_unverified');
            let setter = null;
            try {
                setter = directZustandSetter(node);
            } catch (_err) {
                limitations.add('single_file_binding_unavailable');
            }
            if (setter) {
                collectReturns(setter.callback, setter);
                return;
            }
            // Follow only call wrappers and callback arguments, never option objects,
            // nested action bodies, arbitrary identifiers or executed code.
            const callee = unwrap(node.expression);
            if (callee && ts.isCallExpression(callee)) visitInitializer(callee);
            for (const argument of node.arguments) {
                const value = unwrap(argument);
                if (value && (ts.isCallExpression(value) || ts.isArrowFunction(value) || ts.isFunctionExpression(value))) {
                    visitInitializer(value);
                } else {
                    limitations.add('non_callback_argument_not_resolved');
                }
            }
        } else {
            limitations.add('initializer_shape_not_resolved');
        }
    }
    visitInitializer(initializer, true);
    return {status: 'syntax_observed', members, limitations: [...limitations].sort(),
        runtime_owner_binding: 'not_established', lexical_binding: 'unverified'};
}

function collectImportBindings(sourceFile) {
    const bindings = new Map();

    function bind(localName, source, importedName, kind) {
        if (!localName || !source) return;
        bindings.set(localName, {
            localName,
            source,
            importedName: importedName || localName,
            kind,
        });
    }

    for (const stmt of sourceFile.statements) {
        if (!ts.isImportDeclaration(stmt) || !stmt.moduleSpecifier) continue;
        const source = stmt.moduleSpecifier.getText(sourceFile).replace(/['"`]/g, '');
        const clause = stmt.importClause;
        if (!clause) continue;

        if (clause.name) {
            bind(clause.name.getText(sourceFile), source, 'default', 'default');
        }

        if (clause.namedBindings) {
            if (ts.isNamespaceImport(clause.namedBindings)) {
                bind(clause.namedBindings.name.getText(sourceFile), source, '*', 'namespace');
            } else if (ts.isNamedImports(clause.namedBindings)) {
                for (const el of clause.namedBindings.elements) {
                    const localName = el.name.getText(sourceFile);
                    const importedName = el.propertyName
                        ? el.propertyName.getText(sourceFile)
                        : localName;
                    bind(localName, source, importedName, 'named');
                }
            }
        }
    }

    return bindings;
}

function collectDirectImportBindingEvidence(sourceFile) {
    // Search orientation only: syntax does not prove use, resolution or execution.
    const records = [];
    function add(node, source, localName, importedName, kind, typeOnly) {
        if (!node || !source || !localName) return;
        const line = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
        const endLine = sourceFile.getLineAndCharacterOfPosition(
            Math.max(node.getStart(sourceFile), node.getEnd() - 1)
        ).line + 1;
        records.push({
            source, localName, importedName, kind, typeOnly,
            line, endLine, bindingScope: 'file_top_level_import_declaration'
        });
    }
    for (const stmt of sourceFile.statements) {
        if (!ts.isImportDeclaration(stmt) || !ts.isStringLiteralLike(stmt.moduleSpecifier)) continue;
        const source = stmt.moduleSpecifier.text;
        const clause = stmt.importClause;
        if (!clause) continue;
        if (clause.name) {
            add(clause.name, source, clause.name.text, 'default', 'default', Boolean(clause.isTypeOnly));
        }
        const bindings = clause.namedBindings;
        if (bindings && ts.isNamespaceImport(bindings)) {
            add(bindings.name, source, bindings.name.text, '*', 'namespace', Boolean(clause.isTypeOnly));
        } else if (bindings && ts.isNamedImports(bindings)) {
            for (const element of bindings.elements) {
                add(element, source, element.name.text, element.propertyName?.text || element.name.text,
                    'named', Boolean(clause.isTypeOnly || element.isTypeOnly));
            }
        }
    }
    return records;
}

function getExpressionName(expr, sourceFile) {
    if (!expr) return '';
    if (ts.isIdentifier(expr)) return expr.text;
    if (ts.isPropertyAccessExpression(expr)) return expr.name.getText(sourceFile);
    return '';
}

function getExpressionRootName(expr, sourceFile) {
    if (!expr) return '';
    if (ts.isIdentifier(expr)) return expr.text;
    if (ts.isPropertyAccessExpression(expr)) return getExpressionRootName(expr.expression, sourceFile);
    if (ts.isElementAccessExpression(expr)) return getExpressionRootName(expr.expression, sourceFile);
    return '';
}

function isFunctionLikeExpression(node) {
    return !!node && (ts.isArrowFunction(node) || ts.isFunctionExpression(node));
}

function hasSourcePathSegment(source, segment) {
    const wanted = String(segment || '').toLowerCase();
    if (!wanted) return false;
    return String(source || '')
        .toLowerCase()
        .split(/[\\/]+/)
        .some(part => part === wanted);
}

function hasStoreSourceSignal(source) {
    const parts = String(source || '').toLowerCase().split(/[\\/]+/);
    return parts.some(part => part === 'store' || part === 'stores' || /^use[A-Z0-9_]/i.test(part) && part.endsWith('store'));
}

function isDomainStoreHookName(name) {
    const hookName = String(name || '');
    if (!/^use[A-Z0-9_].*Store$/.test(hookName)) {
        return false;
    }
    if (hookName === 'useStore' || hookName === 'useLegacyStore' || /InStore$/.test(hookName)) {
        return false;
    }
    return true;
}

function isZustandHookCall(callName, source) {
    const name = String(callName || '');
    const importSource = String(source || '').toLowerCase();
    if (name === 'useShallow' || importSource.includes('zustand/react/shallow') || importSource.includes('zustand/shallow')) {
        return false;
    }
    if (importSource.includes('zustand')) {
        return /^use[A-Z0-9_]/.test(name) || name === 'useStore';
    }
    if (name === 'useStore' || name === 'useLegacyStore' || /InStore$/.test(name)) {
        return false;
    }
    if (hasStoreSourceSignal(source) && /^use[A-Z0-9_]/.test(name)) {
        return true;
    }
    if (!importSource) {
        return /^use[A-Z0-9_].*Store$/.test(name);
    }
    return false;
}

function selectorReturnsBroadStore(selectorArg, sourceFile) {
    if (!isFunctionLikeExpression(selectorArg) || !selectorArg.parameters?.length) return false;
    const firstParam = selectorArg.parameters[0].name?.getText(sourceFile);
    if (!firstParam) return false;

    function isBroadExpression(expr) {
        const unwrapped = unwrapExpression(expr);
        if (!unwrapped) return false;
        if (ts.isIdentifier(unwrapped) && unwrapped.getText(sourceFile) === firstParam) {
            return true;
        }
        if (ts.isObjectLiteralExpression(unwrapped)) {
            return unwrapped.properties.some(prop =>
                ts.isSpreadAssignment(prop) && prop.expression.getText(sourceFile) === firstParam
            );
        }
        if (ts.isCallExpression(unwrapped)) {
            const callee = unwrapped.expression.getText(sourceFile);
            if (callee === 'Object.assign') {
                return unwrapped.arguments.some(arg => arg.getText(sourceFile) === firstParam);
            }
        }
        return false;
    }

    if (ts.isArrowFunction(selectorArg) && selectorArg.body && !ts.isBlock(selectorArg.body)) {
        return isBroadExpression(selectorArg.body);
    }
    if (selectorArg.body && ts.isBlock(selectorArg.body)) {
        return selectorArg.body.statements.some(stmt =>
            ts.isReturnStatement(stmt) && stmt.expression && isBroadExpression(stmt.expression)
        );
    }
    return false;
}

function getShallowWrappedSelectorArg(arg, sourceFile, importedFrom) {
    if (!arg || !ts.isCallExpression(arg)) return null;
    const wrapperName = getExpressionName(arg.expression, sourceFile);
    const wrapperSource = String(importedFrom.get(wrapperName) || '').toLowerCase();
    const isShallowWrapper = wrapperName === 'useShallow' || wrapperSource.includes('zustand/react/shallow') || wrapperSource.includes('zustand/shallow');
    if (!isShallowWrapper) return null;
    return arg.arguments?.[0] || null;
}

function isShallowEqualityArg(arg, sourceFile, importedFrom) {
    if (!arg) return false;
    const argName = getExpressionName(arg, sourceFile) || arg.getText(sourceFile);
    const argSource = String(importedFrom.get(argName) || '').toLowerCase();
    return argName === 'shallow' || argName === 'useShallow' || argSource.includes('zustand/react/shallow') || argSource.includes('zustand/shallow');
}

function readLiteralList(node) {
    if (!node) return [];
    if (ts.isArrayLiteralExpression(node)) {
        return node.elements
            .map(el => {
                if (ts.isStringLiteral(el) || ts.isNoSubstitutionTemplateLiteral(el)) return el.text;
                if (ts.isIdentifier(el)) return el.text;
                return el.getText();
            })
            .filter(Boolean);
    }
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
        return [node.text];
    }
    if (ts.isIdentifier(node)) {
        return [node.text];
    }
    if (ts.isCallExpression(node) || ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
        return [node.getText()];
    }
    return [];
}

function readStaticLiteralText(node, sourceFile) {
    if (!node) return '';
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
    if (ts.isNumericLiteral(node)) return node.text;
    if (node.kind === ts.SyntaxKind.TrueKeyword) return 'true';
    if (node.kind === ts.SyntaxKind.FalseKeyword) return 'false';
    if (node.kind === ts.SyntaxKind.NullKeyword) return 'null';
    if (ts.isPrefixUnaryExpression(node) && ts.isNumericLiteral(node.operand)) {
        const op = node.operator === ts.SyntaxKind.MinusToken ? '-' : node.operator === ts.SyntaxKind.PlusToken ? '+' : '';
        if (op) return `${op}${node.operand.text}`;
    }
    return '';
}

function readQueryKeyParts(node, sourceFile) {
    const parts = {
        literal: [],
        refs: [],
        dynamic: [],
    };

    function pushUnique(bucket, value) {
        const text = String(value || '').trim();
        if (text && !bucket.includes(text)) bucket.push(text);
    }

    function readPart(valueNode) {
        if (!valueNode) return;
        if (ts.isSpreadElement(valueNode)) {
            const exprText = valueNode.expression.getText(sourceFile);
            pushUnique(parts.dynamic, exprText);
            if (ts.isIdentifier(valueNode.expression)) {
                pushUnique(parts.refs, valueNode.expression.text);
            }
            return;
        }
        const literalText = readStaticLiteralText(valueNode, sourceFile);
        if (literalText) {
            pushUnique(parts.literal, literalText);
            return;
        }
        if (ts.isIdentifier(valueNode)) {
            pushUnique(parts.refs, valueNode.text);
            return;
        }
        if (ts.isTemplateExpression(valueNode) || ts.isCallExpression(valueNode) || ts.isPropertyAccessExpression(valueNode) || ts.isElementAccessExpression(valueNode)) {
            pushUnique(parts.dynamic, valueNode.getText(sourceFile));
            return;
        }
        pushUnique(parts.dynamic, valueNode.getText(sourceFile));
    }

    if (ts.isArrayLiteralExpression(node)) {
        for (const element of node.elements) {
            readPart(element);
        }
        return parts;
    }
    readPart(node);
    return parts;
}

function collectFunctionLikeDescendants(node, bucket = []) {
    if (!node) return bucket;
    if (isFunctionLikeExpression(node)) {
        bucket.push(node);
        return bucket;
    }
    ts.forEachChild(node, child => collectFunctionLikeDescendants(child, bucket));
    return bucket;
}

function collectZustandTransitionFeatures(createCall, sourceFile) {
    const features = [];
    const callbacks = [];
    for (const arg of createCall.arguments || []) {
        collectFunctionLikeDescendants(arg, callbacks);
    }
    for (const callback of callbacks) {
        const params = callback.parameters || [];
        const setName = params[0]?.name?.getText(sourceFile) || '';
        const getName = params[1]?.name?.getText(sourceFile) || '';
        const bodyText = callback.body ? callback.body.getText(sourceFile) : '';
        if (setName && new RegExp(`\\b${setName}\\s*\\(`).test(bodyText)) {
            features.push('Tech:setState');
        }
        if (getName && new RegExp(`\\b${getName}\\s*\\(`).test(bodyText)) {
            features.push('Tech:getState');
        }
    }
    return features;
}

function collectObjectPropertyValues(node, propNames, sourceFile) {
    if (!node || !ts.isObjectLiteralExpression(node)) return [];
    const values = [];
    for (const prop of node.properties) {
        if (!ts.isPropertyAssignment(prop) || !prop.name) continue;
        const propName = prop.name.getText(sourceFile);
        if (!propNames.includes(propName)) continue;
        for (const value of readLiteralList(prop.initializer)) {
            values.push(value);
        }
    }
    return values;
}

function collectObjectPropertyKeyParts(node, propNames, sourceFile) {
    if (!node || !ts.isObjectLiteralExpression(node)) return { literal: [], refs: [], dynamic: [] };
    const merged = { literal: [], refs: [], dynamic: [] };
    for (const prop of node.properties) {
        if (!ts.isPropertyAssignment(prop) || !prop.name) continue;
        const propName = prop.name.getText(sourceFile);
        if (!propNames.includes(propName)) continue;
        const parts = readQueryKeyParts(prop.initializer, sourceFile);
        mergeKeyParts(merged, parts);
    }
    return merged;
}

function mergeKeyParts(merged, parts) {
    for (const key of ['literal', 'refs', 'dynamic']) {
        for (const value of parts[key] || []) {
            if (value && !merged[key].includes(value)) merged[key].push(value);
        }
    }
    return merged;
}

function hasAnyKeyParts(parts) {
    return Boolean(
        (parts.literal || []).length ||
        (parts.refs || []).length ||
        (parts.dynamic || []).length
    );
}

function collectKeyPartsFromArgument(node, propNames, sourceFile) {
    if (!node) return { literal: [], refs: [], dynamic: [] };
    const objectParts = collectObjectPropertyKeyParts(node, propNames, sourceFile);
    if (hasAnyKeyParts(objectParts)) return objectParts;
    if (ts.isObjectLiteralExpression(node)) return objectParts;
    return readQueryKeyParts(node, sourceFile);
}

function collectQueriesKeyPartsFromArgument(node, sourceFile) {
    const merged = { literal: [], refs: [], dynamic: [] };
    if (!node) return merged;

    function collectQueryEntry(entryNode) {
        if (!entryNode) return;
        if (ts.isSpreadElement(entryNode)) {
            mergeKeyParts(merged, { literal: [], refs: [], dynamic: [entryNode.expression.getText(sourceFile)] });
            return;
        }
        if (ts.isObjectLiteralExpression(entryNode)) {
            mergeKeyParts(merged, collectObjectPropertyKeyParts(entryNode, ['queryKey'], sourceFile));
            return;
        }
        mergeKeyParts(merged, readQueryKeyParts(entryNode, sourceFile));
    }

    if (ts.isArrayLiteralExpression(node)) {
        for (const element of node.elements) collectQueryEntry(element);
        return merged;
    }

    if (ts.isObjectLiteralExpression(node)) {
        for (const prop of node.properties || []) {
            if (!ts.isPropertyAssignment(prop) || !prop.name) continue;
            const propName = prop.name.getText(sourceFile).replace(/['"`]/g, '');
            if (propName !== 'queries') continue;
            const initializer = prop.initializer;
            if (ts.isArrayLiteralExpression(initializer)) {
                for (const element of initializer.elements) collectQueryEntry(element);
            } else {
                mergeKeyParts(merged, readQueryKeyParts(initializer, sourceFile));
            }
        }
    }
    return merged;
}

function addKeyPartFeatures(fileFeatures, prefix, parts) {
    for (const value of parts.literal || []) {
        fileFeatures.add(`${prefix}:${value}`);
    }
    for (const value of parts.refs || []) {
        fileFeatures.add(`${prefix}Ref:${value}`);
    }
    for (const value of parts.dynamic || []) {
        fileFeatures.add(`${prefix}Dynamic:${value}`);
    }
}

function collectConfiguredFrameworkFeatures(sourceFile, importBindings) {
    const fileFeatures = new Set();
    const importedFrom = new Map();
    for (const [localName, binding] of importBindings.entries()) {
        importedFrom.set(localName, binding.source);
    }

    function addConfigured(kind, source, name = '') {
        for (const feature of configuredFeaturesFor(kind, source, name)) {
            fileFeatures.add(feature);
        }
    }

    function visit(node) {
        if (ts.isImportDeclaration(node) && node.moduleSpecifier) {
            const source = node.moduleSpecifier.getText(sourceFile).replace(/['"`]/g, '');
            addConfigured('imports', source);
            const clause = node.importClause;
            if (clause?.name) addConfigured('import_names', source, clause.name.getText(sourceFile));
            if (clause?.namedBindings && ts.isNamedImports(clause.namedBindings)) {
                for (const element of clause.namedBindings.elements) {
                    const importedName = element.propertyName
                        ? element.propertyName.getText(sourceFile)
                        : element.name.getText(sourceFile);
                    addConfigured('import_names', source, importedName);
                }
            }
        } else if (ts.isCallExpression(node)) {
            const callName = getExpressionName(node.expression, sourceFile);
            const rootCallName = getExpressionRootName(node.expression, sourceFile) || callName;
            const source = importedFrom.get(rootCallName) || importedFrom.get(callName) || '';
            addConfigured('calls', source, callName);
            if (rootCallName !== callName) addConfigured('calls', source, rootCallName);
        } else if (ts.isPropertyAssignment(node) && node.name) {
            const propertyName = node.name.getText(sourceFile).replace(/['"`]/g, '');
            let cursor = node.parent;
            while (cursor && !ts.isCallExpression(cursor)) cursor = cursor.parent;
            if (cursor && ts.isCallExpression(cursor)) {
                const callName = getExpressionName(cursor.expression, sourceFile);
                const rootCallName = getExpressionRootName(cursor.expression, sourceFile) || callName;
                const source = importedFrom.get(rootCallName) || importedFrom.get(callName) || '';
                addConfigured('properties', source, propertyName);
            }
        }
        ts.forEachChild(node, visit);
    }

    visit(sourceFile);
    return fileFeatures;
}

function collectStateFlowFeatures(sourceFile, importBindings) {
    const fileFeatures = new Set();
    const importedFrom = new Map();

    for (const [localName, binding] of importBindings.entries()) {
        importedFrom.set(localName, binding.source);
    }

    function objectHasRouteShape(objectNode) {
        if (!objectNode || !ts.isObjectLiteralExpression(objectNode)) return false;
        const routeKeys = new Set();
        for (const prop of objectNode.properties || []) {
            if (!ts.isPropertyAssignment(prop) || !prop.name) continue;
            routeKeys.add(prop.name.getText(sourceFile).replace(/['"`]/g, ''));
        }
        return (
            routeKeys.has('path') ||
            routeKeys.has('index') ||
            routeKeys.has('element') ||
            routeKeys.has('Component') ||
            routeKeys.has('children') ||
            routeKeys.has('errorElement') ||
            routeKeys.has('hydrateFallbackElement') ||
            routeKeys.has('caseSensitive') ||
            routeKeys.has('shouldRevalidate')
        );
    }

    function isRouteObjectProperty(node) {
        if (!node || !ts.isPropertyAssignment(node)) return false;
        if (objectHasRouteShape(node.parent)) return true;

        let cursor = node.parent;
        while (cursor) {
            if (ts.isCallExpression(cursor)) {
                const callName = getExpressionName(cursor.expression, sourceFile);
                const source = importedFrom.get(callName) || '';
                return (
                    source === 'react-router-dom' ||
                    source === 'react-router' ||
                    ['createBrowserRouter', 'createMemoryRouter', 'createHashRouter', 'createRoutesFromElements'].includes(callName)
                );
            }
            cursor = cursor.parent;
        }
        return false;
    }

    function visit(node) {
        if (ts.isCallExpression(node)) {
            const callName = getExpressionName(node.expression, sourceFile);
            const source = importedFrom.get(callName) || '';

            if (
                (callName === 'create' || callName === 'createStore') &&
                (source.includes('zustand') || source.includes('zustand/'))
            ) {
                fileFeatures.add('ZustandStore');
                for (const feature of collectZustandTransitionFeatures(node, sourceFile)) {
                    fileFeatures.add(feature);
                }
            }

            if (callName === 'persist' && source.includes('zustand')) {
                fileFeatures.add('ZustandStore');
            }

            const rootCallName = getExpressionRootName(node.expression, sourceFile) || callName;
            const rootSource = importedFrom.get(rootCallName) || source || '';
            const memberCallName = ts.isPropertyAccessExpression(node.expression)
                ? node.expression.name.getText(sourceFile)
                : '';
            const isImperativeStoreApi = ['getState', 'getInitialState', 'setState', 'subscribe', 'destroy'].includes(memberCallName);
            if (!isImperativeStoreApi && isZustandHookCall(rootCallName, rootSource)) {
                if (node.arguments.length === 0) {
                    fileFeatures.add('Zustand:NoSelector');
                    fileFeatures.add(`Zustand:NoSelector:${rootCallName}`);
                } else {
                    const selectorArg = isFunctionLikeExpression(node.arguments[0])
                        ? node.arguments[0]
                        : getShallowWrappedSelectorArg(node.arguments[0], sourceFile, importedFrom);
                    fileFeatures.add('Tech:selector');
                    fileFeatures.add(`Tech:selector:${rootCallName}`);
                    if (getShallowWrappedSelectorArg(node.arguments[0], sourceFile, importedFrom) || isShallowEqualityArg(node.arguments[1], sourceFile, importedFrom)) {
                        fileFeatures.add('Tech:selector_shallow');
                    }
                    if (selectorArg && selectorReturnsBroadStore(selectorArg, sourceFile)) {
                        fileFeatures.add('Zustand:BroadSelector');
                        fileFeatures.add(`Zustand:BroadSelector:${rootCallName}`);
                    }
                    if (node.arguments.length > 1) {
                        fileFeatures.add('Tech:selector_equality_fn');
                    }
                }
            }

            const reduxLike = ['createSlice', 'configureStore', 'combineSlices'];
            if (reduxLike.includes(callName) && source.includes('@reduxjs/toolkit')) {
                fileFeatures.add('ReduxStore');
                if (callName === 'createSlice') {
                    fileFeatures.add('ReduxSlice');
                }
            }

            if (callName === 'createAsyncThunk' && source.includes('@reduxjs/toolkit')) {
                fileFeatures.add('ReduxAsyncThunk');
            }

            const queryLike = ['useQuery', 'useInfiniteQuery', 'useSuspenseQuery', 'fetchQuery', 'prefetchQuery', 'ensureQueryData', 'queryOptions', 'infiniteQueryOptions'];
            const multiQueryLike = ['useQueries', 'useSuspenseQueries'];
            const mutationLike = ['useMutation', 'executeMutation', 'mutationOptions'];
            if (queryLike.includes(callName)) {
                const firstArg = node.arguments[0];
                addKeyPartFeatures(fileFeatures, 'QueryKey', collectKeyPartsFromArgument(firstArg, ['queryKey'], sourceFile));
            }
            if (multiQueryLike.includes(callName)) {
                const firstArg = node.arguments[0];
                addKeyPartFeatures(fileFeatures, 'QueryKey', collectQueriesKeyPartsFromArgument(firstArg, sourceFile));
            }
            if (mutationLike.includes(callName)) {
                const firstArg = node.arguments[0];
                fileFeatures.add('TanStackMutation');
                addKeyPartFeatures(fileFeatures, 'MutationKey', collectKeyPartsFromArgument(firstArg, ['mutationKey'], sourceFile));
            }

            if (ts.isPropertyAccessExpression(node.expression)) {
                const objectName = node.expression.expression.getText(sourceFile);
                const methodName = node.expression.name.getText(sourceFile);
                const firstArg = node.arguments[0];
                const importedSource = importedFrom.get(objectName) || importedFrom.get(methodName) || '';
                const isQueryClientMethod = ['invalidateQueries', 'prefetchQuery', 'fetchQuery', 'ensureQueryData', 'setQueryData', 'getQueryData', 'cancelQueries', 'removeQueries', 'resetQueries', 'refetchQueries'].includes(methodName);
                if (isQueryClientMethod || importedSource.includes('@tanstack/react-query') || objectName === 'queryClient') {
                    fileFeatures.add(`QueryClientAction:${methodName}`);
                    addKeyPartFeatures(
                        fileFeatures,
                        methodName.toLowerCase().includes('mutation') ? 'MutationKey' : 'QueryKey',
                        collectKeyPartsFromArgument(firstArg, ['queryKey', 'mutationKey'], sourceFile)
                    );
                }
            }
        } else if (ts.isPropertyAssignment(node) && node.name) {
            const propName = node.name.getText(sourceFile);
            if (propName === 'loader' && isRouteObjectProperty(node)) {
                fileFeatures.add('RouteLoader');
            } else if (propName === 'action' && isRouteObjectProperty(node)) {
                fileFeatures.add('RouteAction');
            } else if (propName === 'lazy' && isRouteObjectProperty(node)) {
                fileFeatures.add('RouteLazy');
            }
        } else if (ts.isImportDeclaration(node) && node.moduleSpecifier) {
            const source = node.moduleSpecifier.getText(sourceFile).replace(/['"`]/g, '');
            if ((source === 'react-router-dom' || source === 'react-router') && node.importClause?.namedBindings && ts.isNamedImports(node.importClause.namedBindings)) {
                const importedNames = node.importClause.namedBindings.elements.map(el =>
                    (el.propertyName ? el.propertyName.getText(sourceFile) : el.name.getText(sourceFile))
                );
                if (importedNames.some(name => ['createBrowserRouter', 'createMemoryRouter', 'createHashRouter', 'RouterProvider', 'Outlet', 'Links', 'Meta', 'Scripts', 'ScrollRestoration'].includes(name))) {
                    fileFeatures.add('RouterConfig');
                }
                if (importedNames.includes('redirect') || importedNames.includes('isRouteErrorResponse')) {
                    fileFeatures.add('RouteRedirect');
                }
            }
        }
        ts.forEachChild(node, visit);
    }

    visit(sourceFile);
    return fileFeatures;
}

function collectReactRuntimeFeatures(sourceFile, importBindings) {
    const fileFeatures = new Set();
    const importedFrom = new Map();

    for (const [localName, binding] of importBindings.entries()) {
        importedFrom.set(localName, binding.source);
    }

    function addTagFeature(tagName) {
        if (!tagName) return;
        if (tagName === 'Suspense' || tagName.endsWith('.Suspense')) {
            fileFeatures.add('SuspenseBoundary');
        }
        if (tagName === 'ErrorBoundary' || tagName.endsWith('.ErrorBoundary')) {
            fileFeatures.add('ErrorBoundary');
        }
        if (tagName.endsWith('Provider') || tagName.endsWith('.Provider')) {
            fileFeatures.add('ReactProvider');
        }
    }

    function visit(node) {
        if (ts.isImportDeclaration(node) && node.moduleSpecifier) {
            const source = node.moduleSpecifier.getText(sourceFile).replace(/['"`]/g, '');
            const clause = node.importClause;
            if (clause?.namedBindings && ts.isNamedImports(clause.namedBindings)) {
                const importedNames = clause.namedBindings.elements.map(el =>
                    (el.propertyName ? el.propertyName.getText(sourceFile) : el.name.getText(sourceFile))
                );
                if (source === 'react') {
                    if (importedNames.includes('createContext') || importedNames.includes('useContext')) {
                        fileFeatures.add('ReactContext');
                    }
                    if (importedNames.includes('Suspense')) {
                        fileFeatures.add('SuspenseBoundary');
                    }
                }
                if (source === 'react-error-boundary' && importedNames.includes('ErrorBoundary')) {
                    fileFeatures.add('ErrorBoundary');
                }
            }
        } else if (ts.isCallExpression(node)) {
            const callName = getExpressionName(node.expression, sourceFile);
            const source = importedFrom.get(callName) || '';
            if (callName === 'createContext' && source === 'react') {
                fileFeatures.add('ReactContext');
            }
            if (callName === 'useContext' && source === 'react') {
                fileFeatures.add('ReactContext');
            }
        } else if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
            addTagFeature(node.tagName.getText(sourceFile));
        }
        ts.forEachChild(node, visit);
    }

    visit(sourceFile);
    return fileFeatures;
}

function collectReactHookFlowFeatures(sourceFile, importBindings, symbolAt) {
    const fileFeatures = new Set();
    if (sourceFile.parseDiagnostics?.length) return fileFeatures;
    const hookNames = new Set(['useEffect', 'useLayoutEffect', 'useMemo', 'useCallback']);

    function boundReactHookName(call) {
        if (call.questionDotToken) return null;
        const callee = call.expression;
        const qualified = ts.isPropertyAccessExpression(callee)
            && !callee.questionDotToken && ts.isIdentifier(callee.expression);
        const local = ts.isIdentifier(callee) ? callee : qualified ? callee.expression : null;
        if (!local) return null;
        const binding = importBindings.get(local.text);
        const hook = qualified ? callee.name.text : binding?.importedName;
        if (binding?.source !== 'react' || !hookNames.has(hook)
            || (qualified ? !['namespace', 'default'].includes(binding.kind) : binding.kind !== 'named')) return null;
        const declarations = symbolAt(local)?.declarations || [];
        const declaration = declarations.length === 1 ? declarations[0] : null;
        const clause = declaration && ts.isImportSpecifier(declaration)
            ? declaration.parent.parent
            : declaration && ts.isNamespaceImport(declaration) ? declaration.parent
                : declaration && ts.isImportClause(declaration) ? declaration : null;
        if (!clause || !ts.isImportClause(clause) || clause.isTypeOnly
            || declaration.isTypeOnly || !ts.isImportDeclaration(clause.parent)
            || clause.parent.moduleSpecifier.text !== 'react'
            || declaration.name.text !== binding.localName) return null;
        return hook;
    }

    function bindingNames(nameNode, target) {
        if (!nameNode) return;
        if (ts.isIdentifier(nameNode)) {
            target.add(nameNode.text);
            return;
        }
        if (ts.isObjectBindingPattern(nameNode) || ts.isArrayBindingPattern(nameNode)) {
            for (const element of nameNode.elements || []) {
                if (ts.isBindingElement(element)) bindingNames(element.name, target);
            }
        }
    }

    function enclosingFunctionParameters(node) {
        let cursor = node.parent;
        while (cursor) {
            if (ts.isFunctionLike(cursor)) {
                const names = new Set();
                for (const parameter of cursor.parameters || []) bindingNames(parameter.name, names);
                return names;
            }
            cursor = cursor.parent;
        }
        return new Set();
    }

    function callbackReadsAny(callback, names) {
        const reads = new Set();
        if (!callback || !names.size) return reads;
        function walk(node) {
            if (ts.isIdentifier(node) && names.has(node.text)) reads.add(node.text);
            ts.forEachChild(node, walk);
        }
        walk(callback.body || callback);
        return reads;
    }

    function visit(node) {
        if (ts.isCallExpression(node)) {
            const callName = boundReactHookName(node);
            if (callName) {
                const callLine = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
                if (node.arguments.length < 2) {
                    fileFeatures.add(`Hook:MissingDeps:${callName}:${callLine}`);
                } else {
                    const depsArg = node.arguments[1];
                    if (ts.isArrayLiteralExpression(depsArg)) {
                        if (depsArg.elements.length === 0) {
                            fileFeatures.add(`Hook:EmptyDeps:${callName}`);
                            const callback = node.arguments[0];
                            if (callback && isFunctionLikeExpression(callback)) {
                                const captured = callbackReadsAny(callback, enclosingFunctionParameters(node));
                                if (captured.size) {
                                    fileFeatures.add(`Hook:StaleClosureRisk:${callName}`);
                                    for (const name of captured) fileFeatures.add(`Hook:CapturedReactive:${name}`);
                                }
                            }
                        } else {
                            fileFeatures.add(`Hook:HasDeps:${callName}`);
                        }
                    } else {
                        fileFeatures.add(`Hook:DynamicDeps:${callName}:${callLine}`);
                    }
                }
            }
        }
        ts.forEachChild(node, visit);
    }

    visit(sourceFile);
    return fileFeatures;
}

function collectFormValidationFeatures(sourceFile, importBindings) {
    const fileFeatures = new Set();
    const importedFrom = new Map();
    const formObjects = new Set();
    const formMethods = new Set();

    for (const [localName, binding] of importBindings.entries()) {
        importedFrom.set(localName, binding.source);
    }

    function recordReactHookFormBinding(nameNode) {
        if (!nameNode) {
            return;
        }
        if (ts.isIdentifier(nameNode)) {
            formObjects.add(nameNode.getText(sourceFile));
            return;
        }
        if (ts.isObjectBindingPattern(nameNode)) {
            for (const element of nameNode.elements) {
                if (ts.isIdentifier(element.name)) {
                    const boundName = element.name.getText(sourceFile);
                    const propertyName = element.propertyName ? element.propertyName.getText(sourceFile) : boundName;
                    if (['register', 'handleSubmit', 'control', 'watch', 'setValue', 'getValues', 'formState'].includes(propertyName)) {
                        formMethods.add(boundName);
                    }
                }
            }
        }
    }

    function objectLiteralHasProperty(node, propNames) {
        if (!node || !ts.isObjectLiteralExpression(node)) return false;
        for (const prop of node.properties || []) {
            if (!ts.isPropertyAssignment(prop) || !prop.name) continue;
            const propName = prop.name.getText(sourceFile).replace(/['"`]/g, '');
            if (propNames.includes(propName)) return true;
        }
        return false;
    }

    function visit(node) {
        if (ts.isImportDeclaration(node) && node.moduleSpecifier) {
            const source = node.moduleSpecifier.getText(sourceFile).replace(/['"`]/g, '');
            const clause = node.importClause;
            if (clause?.namedBindings && ts.isNamedImports(clause.namedBindings)) {
                const importedNames = clause.namedBindings.elements.map(el =>
                    (el.propertyName ? el.propertyName.getText(sourceFile) : el.name.getText(sourceFile))
                );
                if (source === 'react-hook-form') {
                    if (importedNames.includes('useForm') || importedNames.includes('FormProvider')) {
                        fileFeatures.add('ReactForm');
                    }
                    if (importedNames.includes('FormProvider')) {
                        fileFeatures.add('FormProvider');
                    }
                    if (importedNames.includes('Controller') || importedNames.includes('useController')) {
                        fileFeatures.add('FormController');
                    }
                    if (importedNames.includes('useFieldArray')) {
                        fileFeatures.add('FormFieldArray');
                    }
                    if (importedNames.includes('useWatch') || importedNames.includes('watch')) {
                        fileFeatures.add('FormWatch');
                    }
                }
                if (source.startsWith('@hookform/resolvers/') && importedNames.some(name => /Resolver$/.test(name))) {
                    fileFeatures.add('FormResolver');
                    fileFeatures.add('ValidationSchema');
                    if (source === '@hookform/resolvers/zod' || importedNames.includes('zodResolver')) {
                        fileFeatures.add('ZodSchema');
                    }
                }
                if (source === 'zod') {
                    fileFeatures.add('ValidationSchema');
                    fileFeatures.add('ZodSchema');
                }
            }
        } else if (ts.isCallExpression(node)) {
            const callName = getExpressionName(node.expression, sourceFile);
            const source = importedFrom.get(callName) || '';
            if (callName === 'useForm' && source === 'react-hook-form') {
                fileFeatures.add('ReactForm');
                if (objectLiteralHasProperty(node.arguments[0], ['resolver'])) {
                    fileFeatures.add('FormResolver');
                    fileFeatures.add('ValidationSchema');
                }
            }
            if (/Resolver$/.test(callName) && source.startsWith('@hookform/resolvers/')) {
                fileFeatures.add('FormResolver');
                fileFeatures.add('ValidationSchema');
                if (source === '@hookform/resolvers/zod' || callName === 'zodResolver') {
                    fileFeatures.add('ZodSchema');
                }
            }
            if ((callName === 'safeParse' || callName === 'parse') && source === 'zod') {
                fileFeatures.add('ValidationSchema');
                fileFeatures.add('ZodSchema');
            }
            if (source === 'react-hook-form') {
                if (callName === 'useController') {
                    fileFeatures.add('ReactForm');
                    fileFeatures.add('FormController');
                }
                if (callName === 'useFieldArray') {
                    fileFeatures.add('ReactForm');
                    fileFeatures.add('FormFieldArray');
                }
                if (callName === 'useWatch') {
                    fileFeatures.add('ReactForm');
                    fileFeatures.add('FormWatch');
                }
            }
            if (ts.isPropertyAccessExpression(node.expression)) {
                const objectName = node.expression.expression.getText(sourceFile);
                const methodName = node.expression.name.getText(sourceFile);
                if (formObjects.has(objectName) && methodName === 'register') {
                    fileFeatures.add('FormFieldRegister');
                    fileFeatures.add('ReactForm');
                }
                if (formObjects.has(objectName) && methodName === 'handleSubmit') {
                    fileFeatures.add('FormSubmitHandler');
                    fileFeatures.add('ReactForm');
                }
                if (formObjects.has(objectName) && methodName === 'watch') {
                    fileFeatures.add('FormWatch');
                    fileFeatures.add('ReactForm');
                }
            } else if (ts.isIdentifier(node.expression)) {
                const callName = node.expression.getText(sourceFile);
                if (formMethods.has(callName) && callName === 'register') {
                    fileFeatures.add('FormFieldRegister');
                    fileFeatures.add('ReactForm');
                }
                if (formMethods.has(callName) && callName === 'handleSubmit') {
                    fileFeatures.add('FormSubmitHandler');
                    fileFeatures.add('ReactForm');
                }
                if (formMethods.has(callName) && callName === 'watch') {
                    fileFeatures.add('FormWatch');
                    fileFeatures.add('ReactForm');
                }
            }
        } else if (ts.isPropertyAccessExpression(node)) {
            if (node.name.getText(sourceFile) === 'errors') {
                const expressionText = node.expression.getText(sourceFile);
                if (expressionText === 'formState' || /\.formState$/.test(expressionText) || /\.formState\.errors$/.test(`${expressionText}.errors`)) {
                    fileFeatures.add('FormErrorState');
                    fileFeatures.add('ReactForm');
                }
            }
        } else if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
            const tagName = node.tagName.getText(sourceFile);
            if (tagName === 'Controller') {
                fileFeatures.add('ReactForm');
                fileFeatures.add('FormController');
            }
        } else if (ts.isVariableDeclaration(node) && node.initializer && ts.isCallExpression(node.initializer)) {
            const callName = getExpressionName(node.initializer.expression, sourceFile);
            const source = importedFrom.get(callName) || '';
            if (callName === 'useForm' && source === 'react-hook-form') {
                fileFeatures.add('ReactForm');
                recordReactHookFormBinding(node.name);
            }
        }
        ts.forEachChild(node, visit);
    }

    visit(sourceFile);
    return fileFeatures;
}

function collectApiBoundaryFeatures(sourceFile, importBindings, targetFile) {
    const fileFeatures = new Set();
    const importedFrom = new Map();
    const normalizedPath = (targetFile || '').replace(/\\/g, '/').toLowerCase();

    for (const [localName, binding] of importBindings.entries()) {
        importedFrom.set(localName, binding.source);
    }

    if (
        normalizedPath.endsWith('.contracts.ts') ||
        normalizedPath.endsWith('.contracts.tsx') ||
        normalizedPath.endsWith('.contract.ts') ||
        normalizedPath.endsWith('.contract.tsx')
    ) {
        fileFeatures.add('ApiContract');
    }

    function visit(node) {
        if (ts.isImportDeclaration(node) && node.moduleSpecifier) {
            const source = node.moduleSpecifier.getText(sourceFile).replace(/['"`]/g, '');
            const clause = node.importClause;
            if (clause?.namedBindings && ts.isNamedImports(clause.namedBindings)) {
                const importedNames = clause.namedBindings.elements.map(el =>
                    (el.propertyName ? el.propertyName.getText(sourceFile) : el.name.getText(sourceFile))
                );
                if (source === 'axios') {
                    fileFeatures.add('ApiClient');
                    if (importedNames.includes('AxiosRequestConfig') || importedNames.includes('AxiosError')) {
                        fileFeatures.add('ApiTransport');
                    }
                }
                if (source.includes('/api.contract') || source.includes('/api.contracts')) {
                    fileFeatures.add('ApiContract');
                }
            }
        } else if (ts.isCallExpression(node)) {
            const callName = getExpressionName(node.expression, sourceFile);
            const source = importedFrom.get(callName) || '';

            if (callName === 'create' && source === 'axios') {
                fileFeatures.add('ApiClient');
                fileFeatures.add('ApiTransport');
            }
            if (callName === 'responseContract') {
                fileFeatures.add('ApiContract');
                fileFeatures.add('ApiResponseContract');
            }
            if (callName === 'safeParse' || callName === 'parse') {
                fileFeatures.add('ApiContract');
            }

            if (ts.isPropertyAccessExpression(node.expression)) {
                const objectName = node.expression.expression.getText(sourceFile);
                const methodName = node.expression.name.getText(sourceFile);

                if (objectName === 'api' && ['get', 'post', 'put', 'patch', 'delete', 'request'].includes(methodName)) {
                    fileFeatures.add('ApiClient');
                    fileFeatures.add('ApiTransport');
                }
                if (methodName === 'use' && node.expression.expression.getText(sourceFile).includes('interceptors')) {
                    fileFeatures.add('ApiInterceptor');
                    fileFeatures.add('ApiClient');
                }
            }
        } else if (ts.isPropertyAccessExpression(node)) {
            const accessText = node.getText(sourceFile);
            if (accessText.includes('interceptors.request') || accessText.includes('interceptors.response')) {
                fileFeatures.add('ApiInterceptor');
                fileFeatures.add('ApiClient');
            }
        }
        ts.forEachChild(node, visit);
    }

    visit(sourceFile);
    return fileFeatures;
}

function collectTestRuntimeFeatures(sourceFile, importBindings, targetFile) {
    const fileFeatures = new Set();
    const importedFrom = new Map();
    const normalizedPath = (targetFile || '').replace(/\\/g, '/').toLowerCase();

    for (const [localName, binding] of importBindings.entries()) {
        importedFrom.set(localName, binding.source);
    }

    if (
        normalizedPath.endsWith('.test.ts') ||
        normalizedPath.endsWith('.test.tsx') ||
        normalizedPath.endsWith('.spec.ts') ||
        normalizedPath.endsWith('.spec.tsx')
    ) {
        fileFeatures.add('TestRuntime');
        fileFeatures.add('UnitTest');
    }

    if (normalizedPath.includes('/cypress/') || normalizedPath.endsWith('cypress.config.ts') || normalizedPath.endsWith('cypress.config.js')) {
        fileFeatures.add('TestRuntime');
        fileFeatures.add('E2ETest');
        fileFeatures.add('CypressRuntime');
    }

    if (normalizedPath.endsWith('jest.config.js') || normalizedPath.endsWith('jest.config.ts')) {
        fileFeatures.add('TestRuntime');
        fileFeatures.add('JestRuntime');
    }

    function visit(node) {
        if (ts.isImportDeclaration(node) && node.moduleSpecifier) {
            const source = node.moduleSpecifier.getText(sourceFile).replace(/['"`]/g, '');
            if (source === '@jest/globals' || source.startsWith('jest')) {
                fileFeatures.add('TestRuntime');
                fileFeatures.add('JestRuntime');
            }
            if (source.startsWith('@testing-library/')) {
                fileFeatures.add('TestRuntime');
                fileFeatures.add('TestingLibrary');
            }
            if (source === 'cypress' || source.startsWith('@cypress/')) {
                fileFeatures.add('TestRuntime');
                fileFeatures.add('CypressRuntime');
                fileFeatures.add('E2ETest');
            }
            if (source === 'playwright' || source.startsWith('@playwright/')) {
                fileFeatures.add('TestRuntime');
                fileFeatures.add('PlaywrightRuntime');
                fileFeatures.add('E2ETest');
            }
            if (source === 'vitest') {
                fileFeatures.add('TestRuntime');
                fileFeatures.add('VitestRuntime');
            }
        } else if (ts.isCallExpression(node)) {
            const callName = getExpressionName(node.expression, sourceFile);
            const source = importedFrom.get(callName) || '';
            if (['describe', 'it', 'test', 'expect', 'beforeEach', 'afterEach'].includes(callName)) {
                if (source === '@jest/globals' || source === 'vitest') {
                    fileFeatures.add('TestRuntime');
                    if (source === '@jest/globals') fileFeatures.add('JestRuntime');
                    if (source === 'vitest') fileFeatures.add('VitestRuntime');
                }
            }
        }
        ts.forEachChild(node, visit);
    }

    visit(sourceFile);
    return fileFeatures;
}

function collectStylingFeatures(sourceFile, importBindings, targetFile) {
    const fileFeatures = new Set();
    const normalizedPath = (targetFile || '').replace(/\\/g, '/').toLowerCase();

    if (normalizedPath.endsWith('.module.css')) {
        fileFeatures.add('StyleSystem');
        fileFeatures.add('CssModule');
    }
    if (normalizedPath.endsWith('.module.scss') || normalizedPath.endsWith('.module.sass')) {
        fileFeatures.add('StyleSystem');
        fileFeatures.add('SassModule');
    }

    function visit(node) {
        if (ts.isImportDeclaration(node) && node.moduleSpecifier) {
            const source = node.moduleSpecifier.getText(sourceFile).replace(/['"`]/g, '');
            if (source.endsWith('.module.css')) {
                fileFeatures.add('StyleSystem');
                fileFeatures.add('CssModule');
            }
            if (source.endsWith('.module.scss') || source.endsWith('.module.sass')) {
                fileFeatures.add('StyleSystem');
                fileFeatures.add('SassModule');
            }
            if (source === 'styled-components' || source.startsWith('@emotion/') || source.startsWith('@mui/') || source.startsWith('@chakra-ui/') || source.startsWith('@radix-ui/')) {
                fileFeatures.add('StyleSystem');
                fileFeatures.add('UiKit');
            }
            if (source === 'classnames' || source === 'clsx') {
                fileFeatures.add('StyleSystem');
            }
        } else if (ts.isPropertyAccessExpression(node)) {
            const accessText = node.getText(sourceFile);
            if (accessText.startsWith('styles.')) {
                fileFeatures.add('StyleSystem');
                fileFeatures.add('DesignToken');
            }
        } else if (ts.isElementAccessExpression(node)) {
            const exprText = node.expression.getText(sourceFile);
            if (exprText === 'styles') {
                fileFeatures.add('StyleSystem');
                fileFeatures.add('DesignToken');
            }
        }
        ts.forEachChild(node, visit);
    }

    visit(sourceFile);
    return fileFeatures;
}

function extractSignature(node, sourceFile) {
    try {
        if (ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node)) {
            const name = node.name ? node.name.getText(sourceFile) : 'anonymous';
            const params = node.parameters.map(p => p.getText(sourceFile)).join(', ');
            const returnType = node.type ? `: ${node.type.getText(sourceFile)}` : '';
            return `function ${name}(${params})${returnType}`;
        }
        if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
            const params = node.parameters.map(p => p.getText(sourceFile)).join(', ');
            const returnType = node.type ? `: ${node.type.getText(sourceFile)}` : '';
            return `(${params})${returnType} => ...`;
        }
        if (ts.isVariableDeclaration(node)) {
            const name = node.name.getText(sourceFile);
            const type = node.type ? `: ${node.type.getText(sourceFile)}` : '';
            return `const ${name}${type} = ...`;
        }
        return node.getText(sourceFile).split('{')[0].trim();
    } catch (e) { return ""; }
}

function generateDna(content) {
    return crypto.createHash('sha256').update(content).digest('hex');
}

function stripComments(text) {
    return String(text || '')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

function compactLogicText(text) {
    return stripComments(text)
        .replace(/^\s*["']use client["'];?\s*$/gm, '')
        .replace(/^\s*["']use server["'];?\s*$/gm, '')
        .replace(/\s+/g, '')
        .replace(/;+$/g, '')
        .trim();
}

function expressionLogicText(node, sourceFile) {
    if (!node) return '';
    return compactLogicText(node.getText(sourceFile));
}

function unwrapExpression(node) {
    let current = node;
    while (
        current &&
        (
            ts.isParenthesizedExpression(current) ||
            ts.isAsExpression(current) ||
            ts.isTypeAssertionExpression(current) ||
            ts.isNonNullExpression(current) ||
            (ts.isSatisfiesExpression && ts.isSatisfiesExpression(current))
        )
    ) {
        current = current.expression;
    }
    return current || node;
}

function getCallExpressionName(expr, sourceFile) {
    if (!expr) return '';
    if (ts.isIdentifier(expr)) return expr.text;
    if (ts.isPropertyAccessExpression(expr)) return expr.name.getText(sourceFile);
    return expr.getText ? expr.getText(sourceFile) : '';
}

function frameworkTag(kind, value) {
    const bucket = LANGUAGE_AGNOSTIC_SYMBOLS.framework_tags?.[kind] || {};
    return bucket[value] || '';
}

function collectSourceDirectiveTags(sourceFile) {
    const tags = [];
    for (const stmt of sourceFile.statements || []) {
        if (!ts.isExpressionStatement(stmt)) break;
        const expr = stmt.expression;
        if (!(ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr))) break;
        const tag = frameworkTag('directives', expr.text);
        if (tag) tags.push(tag);
    }
    return tags;
}

function normalizeFunctionLikeBody(node, sourceFile) {
    if (!node || !node.body) return '';
    const body = node.body;
    if (ts.isBlock(body)) {
        const statements = body.statements || [];
        if (statements.length === 1 && ts.isReturnStatement(statements[0]) && statements[0].expression) {
            return `return(${expressionLogicText(statements[0].expression, sourceFile)})`;
        }
        return compactLogicText(body.getText(sourceFile));
    }
    return `return(${expressionLogicText(body, sourceFile)})`;
}

function logicTextForNode(node, sourceFile) {
    if (!node) return '';
    if (ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node)) {
        return normalizeFunctionLikeBody(node, sourceFile);
    }
    if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
        return normalizeFunctionLikeBody(node, sourceFile);
    }
    if (ts.isVariableDeclaration(node)) {
        const init = unwrapExpression(node.initializer);
        if (!init) return compactLogicText(node.getText(sourceFile));
        if (ts.isArrowFunction(init) || ts.isFunctionExpression(init)) {
            return normalizeFunctionLikeBody(init, sourceFile);
        }
        if (ts.isCallExpression(init)) {
            const callName = getCallExpressionName(init.expression, sourceFile);
            const wrapperTag = frameworkTag('wrappers', callName);
            const firstArg = unwrapExpression(init.arguments?.[0]);
            if (wrapperTag && firstArg && (ts.isArrowFunction(firstArg) || ts.isFunctionExpression(firstArg))) {
                return normalizeFunctionLikeBody(firstArg, sourceFile);
            }
        }
        return expressionLogicText(init, sourceFile);
    }
    if (ts.isClassDeclaration(node) || ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node)) {
        return compactLogicText(extractSignature(node, sourceFile));
    }
    if (ts.isExportAssignment(node)) {
        return expressionLogicText(node.expression, sourceFile);
    }
    return compactLogicText(node.getText(sourceFile));
}

function collectWrapperTags(node, sourceFile) {
    const tags = [];
    if (ts.isVariableDeclaration(node)) {
        const init = unwrapExpression(node.initializer);
        if (init && ts.isCallExpression(init)) {
            const tag = frameworkTag('wrappers', getCallExpressionName(init.expression, sourceFile));
            if (tag) tags.push(tag);
        }
    }
    return tags;
}

function canonicalSymbolType(rawType, name, features = [], frameworkTags = []) {
    const raw = String(rawType || '');
    const symbolName = String(name || '');
    const canonical = new Set(LANGUAGE_AGNOSTIC_SYMBOLS.canonical_types || []);
    const tsMap = LANGUAGE_AGNOSTIC_SYMBOLS.raw_type_map?.typescript || {};
    if (raw === 'Hook' || symbolName.startsWith('use')) return 'hook';
    if (raw === 'Component') return 'component';
    if (frameworkTags.some(tag => ['react_callback_wrapper', 'react_memo_wrapper'].includes(tag))) return 'function';
    if (frameworkTags.some(tag => ['react_memo_component', 'react_forward_ref', 'react_lazy_boundary'].includes(tag))) return 'component';
    if ((raw === 'Function' || raw === 'Arrow') && /^[A-Z]/.test(symbolName) && features.includes('Feature:UIComponent')) {
        return 'component';
    }
    const mapped = tsMap[raw] || '';
    if (mapped && canonical.has(mapped)) return mapped;
    const lowered = raw.toLowerCase();
    return canonical.has(lowered) ? lowered : 'metadata';
}

const REACT_RENDER_EVIDENCE_CACHE = new WeakMap();

function hasDirectReactRenderEvidence(node, sourceFile) {
    if (!node) return false;
    if (REACT_RENDER_EVIDENCE_CACHE.has(node)) return REACT_RENDER_EVIDENCE_CACHE.get(node);
    let found = false;
    function walk(inner) {
        if (found) return;
        if (inner !== node && ts.isFunctionLike(inner)) return;
        if (
            ts.isJsxElement(inner)
            || ts.isJsxSelfClosingElement(inner)
            || ts.isJsxFragment(inner)
        ) {
            found = true;
            return;
        }
        if (
            ts.isCallExpression(inner)
            && getExpressionName(inner.expression, sourceFile) === 'createElement'
        ) {
            found = true;
            return;
        }
        ts.forEachChild(inner, walk);
    }
    walk(node.body || node);
    REACT_RENDER_EVIDENCE_CACHE.set(node, found);
    return found;
}

function hasDeclaredReactComponentType(functionNode, declaration, sourceFile) {
    const typeNodes = [functionNode?.type, declaration?.type].filter(Boolean);
    return typeNodes.some(typeNode =>
        /^(?:React\.)?(?:FC|FunctionComponent|ComponentType)(?:<|$)|^(?:JSX\.)?Element(?:<|$)|^ReactNode$/.test(
            typeNode.getText(sourceFile).replace(/\s+/g, '')
        )
    );
}

function hasReactComponentEvidence(functionNode, declaration, sourceFile) {
    return hasDirectReactRenderEvidence(functionNode, sourceFile)
        || hasDeclaredReactComponentType(functionNode, declaration, sourceFile);
}

function semanticSignature(node, sourceFile, type, name, frameworkTags = []) {
    const rawSignature = extractSignature(node, sourceFile);
    let paramCount = 0;
    let returnType = '';
    if (node && node.parameters) {
        paramCount = node.parameters.length;
    } else if (ts.isVariableDeclaration(node)) {
        const init = unwrapExpression(node.initializer);
        const target = init && ts.isCallExpression(init)
            ? unwrapExpression(init.arguments?.[0])
            : init;
        if (target && target.parameters) paramCount = target.parameters.length;
        if (target && target.type) returnType = target.type.getText(sourceFile);
    }
    if (node && node.type) returnType = node.type.getText(sourceFile);
    return `${canonicalSymbolType(type, name, [], frameworkTags)}:${name || 'anonymous'}(${paramCount})${returnType ? `:${returnType}` : ''}|${compactLogicText(rawSignature)}`;
}

function buildLogicIdentity(name, type, node, sourceFile) {
    const frameworkTags = [
        ...collectSourceDirectiveTags(sourceFile),
        ...collectWrapperTags(node, sourceFile),
    ];
    const exportTag = frameworkTag('exports', name);
    if (exportTag) frameworkTags.push(exportTag);
    const logicText = logicTextForNode(node, sourceFile);
    const normalizedLogic = logicText || compactLogicText(node?.getText ? node.getText(sourceFile) : '');
    const uniqueTags = [
        ...new Set(
            frameworkTags
                .filter(tag => typeof tag === 'string')
                .map(tag => tag.trim())
                .filter(Boolean)
        )
    ].sort();
    const profile = LANGUAGE_AGNOSTIC_SYMBOLS.normalization_profiles?.[NORMALIZATION_PROFILE] || {};
    return {
        logicDna: generateDna(normalizedLogic),
        semanticSignature: semanticSignature(node, sourceFile, type, name, uniqueTags),
        frameworkTags: uniqueTags,
        normalizationProfile: NORMALIZATION_PROFILE,
        semanticDepth: profile.semantic_depth || 'logic_normalized',
        logicDnaKind: profile.logic_dna_kind || 'normalized_body',
        normalizationConfidence: profile.normalization_confidence || 'high',
        parserKind: profile.parser_kind || 'typescript_compiler_api',
        parserVersion: ts.version || 'unknown',
    };
}

function collectReactMutationContextFeatures(sourceFile) {
    const fileFeatures = new Set();
    const synchronousRenderCallbacks = new Set([
        'map', 'flatMap', 'filter', 'reduce', 'reduceRight', 'forEach',
        'find', 'findIndex', 'some', 'every', 'sort', 'useMemo', 'useCallback'
    ]);
    const effectCalls = new Set(['useEffect', 'useLayoutEffect', 'useInsertionEffect']);
    const assignmentOperators = new Set([
        ts.SyntaxKind.EqualsToken,
        ts.SyntaxKind.PlusEqualsToken,
        ts.SyntaxKind.MinusEqualsToken,
        ts.SyntaxKind.AsteriskEqualsToken,
        ts.SyntaxKind.SlashEqualsToken,
        ts.SyntaxKind.PercentEqualsToken,
        ts.SyntaxKind.AmpersandEqualsToken,
        ts.SyntaxKind.BarEqualsToken,
        ts.SyntaxKind.CaretEqualsToken,
        ts.SyntaxKind.LessThanLessThanEqualsToken,
        ts.SyntaxKind.GreaterThanGreaterThanEqualsToken,
        ts.SyntaxKind.GreaterThanGreaterThanGreaterThanEqualsToken,
        ts.SyntaxKind.AsteriskAsteriskEqualsToken,
        ts.SyntaxKind.AmpersandAmpersandEqualsToken,
        ts.SyntaxKind.BarBarEqualsToken,
        ts.SyntaxKind.QuestionQuestionEqualsToken,
    ]);

    function functionName(node) {
        if (node.name && ts.isIdentifier(node.name)) return node.name.text;
        if (ts.isVariableDeclaration(node.parent) && ts.isIdentifier(node.parent.name)) {
            return node.parent.name.text;
        }
        if (
            ts.isCallExpression(node.parent)
            && ts.isVariableDeclaration(node.parent.parent)
            && ts.isIdentifier(node.parent.parent.name)
        ) {
            return node.parent.parent.name.text;
        }
        return '';
    }

    function isDefaultExportFunction(node) {
        return Boolean(node.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.DefaultKeyword));
    }

    function isWrapperComponentCallback(node) {
        const parent = node.parent;
        if (!parent || !ts.isCallExpression(parent) || !parent.arguments.includes(node)) return false;
        const callName = getExpressionName(parent.expression, sourceFile);
        return ['memo', 'React.memo', 'forwardRef', 'React.forwardRef'].includes(callName);
    }

    function isComponentRenderFunction(node) {
        if (!hasReactComponentEvidence(node, node.parent, sourceFile)) return false;
        const name = functionName(node);
        return /^[A-Z]/.test(name) || isDefaultExportFunction(node) || isWrapperComponentCallback(node);
    }

    function enclosingFunction(node) {
        let cursor = node.parent;
        while (cursor) {
            if (ts.isFunctionLike(cursor)) return cursor;
            cursor = cursor.parent;
        }
        return null;
    }

    function enclosingComponentFunction(node) {
        let cursor = node.parent;
        while (cursor) {
            if (ts.isFunctionLike(cursor) && isComponentRenderFunction(cursor)) return cursor;
            cursor = cursor.parent;
        }
        return null;
    }

    function callbackCallName(fn) {
        const parent = fn?.parent;
        if (!parent || !ts.isCallExpression(parent) || !parent.arguments.includes(fn)) return '';
        return getExpressionName(parent.expression, sourceFile);
    }

    function isJsxEventCallback(fn) {
        let cursor = fn?.parent;
        while (cursor && !ts.isFunctionLike(cursor)) {
            if (ts.isJsxAttribute(cursor)) {
                const name = cursor.name?.getText(sourceFile) || '';
                return /^on[A-Z]/.test(name);
            }
            cursor = cursor.parent;
        }
        return false;
    }

    function assignmentContext(node) {
        const fn = enclosingFunction(node);
        if (!fn) return 'module';
        const callName = callbackCallName(fn);
        if (effectCalls.has(callName)) return 'effect';
        const name = functionName(fn);
        if (isJsxEventCallback(fn) || /^(?:handle|on)[A-Z]/.test(name)) return 'event';
        if (isComponentRenderFunction(fn)) return 'render';
        if (synchronousRenderCallbacks.has(callName) && enclosingComponentFunction(fn)) return 'render';
        return 'unresolved';
    }

    function isMutableAssignment(node) {
        if (ts.isBinaryExpression(node)) {
            const operator = node.operatorToken.kind;
            return assignmentOperators.has(operator)
                && (ts.isPropertyAccessExpression(node.left) || ts.isElementAccessExpression(node.left));
        }
        if (ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node)) {
            return [ts.SyntaxKind.PlusPlusToken, ts.SyntaxKind.MinusMinusToken].includes(node.operator)
                && (ts.isPropertyAccessExpression(node.operand) || ts.isElementAccessExpression(node.operand));
        }
        return false;
    }

    function visit(node) {
        if (isMutableAssignment(node)) {
            const line = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
            fileFeatures.add(`React:MutableAssignment:${assignmentContext(node)}:${line}`);
        }
        ts.forEachChild(node, visit);
    }

    visit(sourceFile);
    return fileFeatures;
}

function collectReactResponseStateFeatures(sourceFile, sourceCode, importBindings, symbolAt) {
    const features = new Set();
    if (sourceFile.parseDiagnostics?.length
        || !sourceCode.includes('useState')
        || !sourceCode.includes('fetch')
        || !/\.json\s*\(/.test(sourceCode)) return features;

    const stateSetters = new Set();
    const fetchResponses = new Map();
    const jsonValues = new Map();

    function blockOf(node) {
        for (let cursor = node.parent; cursor; cursor = cursor.parent) {
            if (ts.isBlock(cursor) || ts.isSourceFile(cursor)) return cursor;
        }
        return null;
    }

    function isConstDeclaration(node) {
        return ts.isVariableDeclarationList(node.parent)
            && Boolean(node.parent.flags & ts.NodeFlags.Const);
    }

    function isReactUseState(call) {
        if (!call || !ts.isCallExpression(call)) return false;
        const callee = call.expression;
        if (ts.isIdentifier(callee)) {
            const binding = importBindings.get(callee.text);
            return binding?.source === 'react' && binding.importedName === 'useState';
        }
        if (ts.isPropertyAccessExpression(callee) && callee.name.text === 'useState'
            && ts.isIdentifier(callee.expression)) {
            const binding = importBindings.get(callee.expression.text);
            return binding?.source === 'react' && binding.kind === 'namespace';
        }
        return false;
    }

    function visit(node, callback) {
        callback(node);
        ts.forEachChild(node, child => visit(child, callback));
    }

    visit(sourceFile, node => {
        if (!ts.isVariableDeclaration(node) || !isConstDeclaration(node)) return;
        const init = unwrapExpression(node.initializer);
        if (ts.isArrayBindingPattern(node.name) && isReactUseState(init)
            && init.typeArguments?.length === 1) {
            const declaredType = init.typeArguments[0].getText(sourceFile);
            const setter = node.name.elements[1]?.name;
            if (setter && ts.isIdentifier(setter) && !/\b(?:any|unknown)\b/.test(declaredType)) {
                const binding = symbolAt(setter);
                if (binding) stateSetters.add(binding);
            }
        }
        if (!ts.isIdentifier(node.name) || !init || !ts.isAwaitExpression(init)) return;
        const call = unwrapExpression(init.expression);
        if (!ts.isCallExpression(call) || !ts.isIdentifier(call.expression)
            || call.expression.text !== 'fetch' || symbolAt(call.expression)) return;
        const binding = symbolAt(node.name);
        if (binding) fetchResponses.set(binding, {node, block: blockOf(node)});
    });

    function responseForJsonAwait(expression, block) {
        const awaited = unwrapExpression(expression);
        if (!awaited || !ts.isAwaitExpression(awaited)) return null;
        const call = unwrapExpression(awaited.expression);
        if (!ts.isCallExpression(call) || call.arguments.length
            || !ts.isPropertyAccessExpression(call.expression)
            || call.expression.name.text !== 'json'
            || !ts.isIdentifier(call.expression.expression)) return null;
        const response = fetchResponses.get(symbolAt(call.expression.expression));
        return response?.block === block
            && response.node.getStart(sourceFile) < awaited.getStart(sourceFile)
            ? response : null;
    }

    visit(sourceFile, node => {
        if (!ts.isVariableDeclaration(node) || !isConstDeclaration(node)
            || !ts.isIdentifier(node.name)) return;
        if (!responseForJsonAwait(node.initializer, blockOf(node))) return;
        const binding = symbolAt(node.name);
        let initializer = node.initializer;
        while (initializer && ts.isParenthesizedExpression(initializer)) initializer = initializer.expression;
        const initializerCast = initializer && (ts.isAsExpression(initializer)
            || ts.isTypeAssertionExpression(initializer));
        if (binding) jsonValues.set(binding, {
            node,
            block: blockOf(node),
            explicitAny: node.type?.kind === ts.SyntaxKind.AnyKeyword,
            unannotated: !node.type && !initializerCast,
        });
    });

    function hasPriorParserUse(raw, sink) {
        if (!ts.isBlock(raw.block)) return false;
        return raw.block.statements.some(statement => {
            if (statement.getStart(sourceFile) <= raw.node.getStart(sourceFile)
                || statement.getStart(sourceFile) >= sink.getStart(sourceFile)) return false;
            // A prior parser use may validate this value, even when its result is
            // assigned or checked in an if condition. Prefer a missed advisory
            // over claiming the local path had no validation evidence.
            const candidate = ts.isExpressionStatement(statement)
                ? statement.expression
                : ts.isVariableStatement(statement)
                ? statement.declarationList
                : ts.isIfStatement(statement)
                ? statement.expression
                : null;
            if (!candidate) return false;
            let found = false;
            function visitParser(node) {
                if (found) return;
                if (ts.isCallExpression(node)
                    && ts.isPropertyAccessExpression(node.expression)
                    && ['parse', 'safeParse'].includes(node.expression.name.text)
                    && node.arguments.some(arg => {
                        const value = unwrapExpression(arg);
                        return ts.isIdentifier(value) && symbolAt(value) === symbolAt(raw.node.name);
                    })) {
                    found = true;
                    return;
                }
                ts.forEachChild(node, visitParser);
            }
            visitParser(candidate);
            return found;
        });
    }

    visit(sourceFile, node => {
        if (!ts.isCallExpression(node) || !ts.isIdentifier(node.expression)
            || node.arguments.length !== 1 || !stateSetters.has(symbolAt(node.expression))) return;
        let argument = node.arguments[0];
        while (ts.isParenthesizedExpression(argument)) argument = argument.expression;
        const explicitCast = ts.isAsExpression(argument) || ts.isTypeAssertionExpression(argument);
        const value = unwrapExpression(explicitCast ? argument.expression : argument);
        const sinkLine = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
        const directResponse = !explicitCast ? responseForJsonAwait(value, blockOf(node)) : null;
        if (directResponse) {
            const producerLine = sourceFile.getLineAndCharacterOfPosition(
                directResponse.node.getStart(sourceFile)
            ).line + 1;
            features.add(`React:ExternalResponseDirectState:${sinkLine}:${producerLine}`);
            return;
        }
        if (!ts.isIdentifier(value)) return;
        const raw = jsonValues.get(symbolAt(value));
        if (!raw || (!explicitCast && !raw.explicitAny && !raw.unannotated)
            || raw.block !== blockOf(node) || hasPriorParserUse(raw, node)) return;
        const producerLine = sourceFile.getLineAndCharacterOfPosition(raw.node.getStart(sourceFile)).line + 1;
        if (producerLine < sinkLine) {
            const kind = explicitCast ? 'StateCast' : raw.explicitAny ? 'AnyState' : 'DirectState';
            features.add(`React:ExternalResponse${kind}:${sinkLine}:${producerLine}`);
        }
    });
    return features;
}

function createZodObjectBroaderCastCollector(sourceFile, targetFile, importBindings, symbolAt) {
    if (sourceFile.parseDiagnostics?.length || !/\.(?:ts|tsx|mts|cts)$/i.test(targetFile)) {
        return () => {};
    }
    const schemas = new Map();
    const types = new Map();
    const simpleName = node => ts.isIdentifier(node)
        && /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(node.text);

    function zodOwner(owner) {
        if (!simpleName(owner)) return false;
        const binding = importBindings.get(owner.text);
        if (!binding || binding.source !== 'zod'
            || !((binding.kind === 'named' && binding.importedName === 'z')
                || binding.kind === 'namespace')) return false;
        const declarations = symbolAt(owner)?.declarations || [];
        if (declarations.length !== 1) return false;
        const declaration = declarations[0];
        const clause = binding.kind === 'named' && ts.isImportSpecifier(declaration)
            ? declaration.parent.parent
            : binding.kind === 'namespace' && ts.isNamespaceImport(declaration)
                ? declaration.parent : null;
        return !!clause && ts.isImportClause(clause)
            && !clause.isTypeOnly && !declaration.isTypeOnly
            && ts.isImportDeclaration(clause.parent)
            && clause.parent.moduleSpecifier.text === 'zod';
    }

    for (const statement of sourceFile.statements) {
        if ((ts.isInterfaceDeclaration(statement) && !statement.heritageClauses
            && !statement.typeParameters)
            || (ts.isTypeAliasDeclaration(statement) && !statement.typeParameters
                && ts.isTypeLiteralNode(statement.type))) {
            const members = ts.isInterfaceDeclaration(statement)
                ? statement.members : statement.type.members;
            if (!members.length || members.some(member =>
                !ts.isPropertySignature(member) || !simpleName(member.name))) continue;
            const required = members.filter(member => !member.questionToken)
                .map(member => member.name.text);
            if (simpleName(statement.name)) {
                types.set(statement.name.text, {node: statement.name, required});
            }
        }
        if (!ts.isVariableStatement(statement)
            || !(statement.declarationList.flags & ts.NodeFlags.Const)) continue;
        for (const declaration of statement.declarationList.declarations) {
            if (!simpleName(declaration.name)) continue;
            const call = declaration.initializer;
            if (!call || !ts.isCallExpression(call) || call.arguments.length !== 1
                || !ts.isPropertyAccessExpression(call.expression)
                || call.expression.name.text !== 'object'
                || !zodOwner(call.expression.expression)
                || !ts.isObjectLiteralExpression(call.arguments[0])) continue;
            const properties = call.arguments[0].properties;
            if (!properties.length || properties.some(property =>
                !ts.isPropertyAssignment(property) || !simpleName(property.name))) continue;
            schemas.set(declaration.name.text, {
                node: declaration.name,
                keys: new Set(properties.map(property => property.name.text)),
            });
        }
    }

    return function collect(node, fileFeatures) {
        if (!ts.isAsExpression(node) || !ts.isTypeReferenceNode(node.type)
            || !simpleName(node.type.typeName) || node.type.typeArguments?.length) return;
        const parsed = node.expression;
        if (!ts.isCallExpression(parsed) || parsed.arguments.length !== 1
            || !ts.isPropertyAccessExpression(parsed.expression)
            || parsed.expression.name.text !== 'parse'
            || !simpleName(parsed.expression.expression)) return;
        const schemaRef = parsed.expression.expression;
        const schema = schemas.get(schemaRef.text);
        const type = types.get(node.type.typeName.text);
        if (!schema || !type || schema.node.getStart(sourceFile) >= node.getStart(sourceFile)
            || type.node.getStart(sourceFile) >= node.getStart(sourceFile)
            || symbolAt(schemaRef) !== symbolAt(schema.node)
            || symbolAt(node.type.typeName) !== symbolAt(type.node)
            || (symbolAt(type.node)?.declarations || []).length !== 1) return;
        const missing = type.required.filter(key => !schema.keys.has(key)).sort();
        if (!missing.length) return;
        const schemaLine = sourceFile.getLineAndCharacterOfPosition(schema.node.getStart(sourceFile)).line + 1;
        const castLine = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
        if (schemaLine < castLine) {
            fileFeatures.add(`TypeScript:ZodObjectBroaderCast:${schemaRef.text}:${node.type.typeName.text}:${missing[0]}:${schemaLine}:${castLine}`);
        }
    };
}

function createFiniteNumberGuardCollector(sourceFile, targetFile, symbolAt) {
    if (sourceFile.parseDiagnostics?.length || !/\.(?:ts|tsx|mts|cts)$/i.test(targetFile)) {
        return () => {};
    }

    function nonNumberSubject(expression) {
        const value = unwrapExpression(expression);
        if (!value || !ts.isBinaryExpression(value)
            || value.operatorToken.kind !== ts.SyntaxKind.ExclamationEqualsEqualsToken) return null;
        const left = unwrapExpression(value.left);
        const right = unwrapExpression(value.right);
        for (const [typeOf, literal] of [[left, right], [right, left]]) {
            if (ts.isTypeOfExpression(typeOf)
                && ts.isIdentifier(unwrapExpression(typeOf.expression))
                && ts.isStringLiteral(literal) && literal.text === 'number') {
                return unwrapExpression(typeOf.expression);
            }
        }
        return null;
    }

    function nanOnlySubject(expression) {
        const value = unwrapExpression(expression);
        if (!value || !ts.isCallExpression(value) || value.arguments.length !== 1
            || !ts.isPropertyAccessExpression(value.expression)
            || value.expression.name.text !== 'isNaN') return null;
        const owner = unwrapExpression(value.expression.expression);
        const subject = unwrapExpression(value.arguments[0]);
        return ts.isIdentifier(owner) && owner.text === 'Number' && !symbolAt(owner)
            && ts.isIdentifier(subject) ? subject : null;
    }

    function guardSubject(statement) {
        if (!ts.isIfStatement(statement) || statement.elseStatement) return null;
        const rejection = statement.thenStatement;
        if (!ts.isThrowStatement(rejection)
            && !(ts.isBlock(rejection) && rejection.statements.length === 1
                && ts.isThrowStatement(rejection.statements[0]))) return null;
        const condition = unwrapExpression(statement.expression);
        if (!ts.isBinaryExpression(condition)
            || condition.operatorToken.kind !== ts.SyntaxKind.BarBarToken) return null;
        for (const [typeCheck, nanCheck] of [
            [condition.left, condition.right], [condition.right, condition.left]
        ]) {
            const nonNumber = nonNumberSubject(typeCheck);
            const nanOnly = nanOnlySubject(nanCheck);
            if (nonNumber && nanOnly && nonNumber.text === nanOnly.text
                && symbolAt(nonNumber) && symbolAt(nonNumber) === symbolAt(nanOnly)) {
                return nonNumber;
            }
        }
        return null;
    }

    return function collect(node, fileFeatures) {
        if (!ts.isBlock(node) || node.statements.length !== 2
            || !ts.isFunctionLike(node.parent) || node.parent.body !== node) return;
        const [guard, sink] = node.statements;
        const subject = guardSubject(guard);
        if (!subject || !ts.isReturnStatement(sink) || !sink.expression) return;
        const returned = unwrapExpression(sink.expression);
        if (!ts.isIdentifier(returned) || returned.text !== subject.text
            || symbolAt(returned) !== symbolAt(subject)) return;
        const guardLine = sourceFile.getLineAndCharacterOfPosition(guard.getStart(sourceFile)).line + 1;
        const sinkLine = sourceFile.getLineAndCharacterOfPosition(sink.getStart(sourceFile)).line + 1;
        if (guardLine < sinkLine) {
            fileFeatures.add(`TypeScript:FiniteNumberNaNOnlyGuard:${subject.text}:${guardLine}:${sinkLine}`);
        }
    };
}

function createUnawaitedAsyncHelperCollector(sourceFile, targetFile, symbolAt) {
    if (sourceFile.parseDiagnostics?.length || !/\.(?:ts|tsx|mts|cts)$/i.test(targetFile)) {
        return () => {};
    }
    const helpers = new Map();
    function registerHelper(name) {
        const matches = helpers.get(name.text) || [];
        matches.push(name);
        helpers.set(name.text, matches);
    }
    function isAsync(fn) {
        return !!fn.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.AsyncKeyword);
    }
    function hasOwnAwait(body) {
        let found = false;
        function visit(node) {
            if (found || ts.isFunctionLike(node)) return;
            if (ts.isAwaitExpression(node)) {
                found = true;
                return;
            }
            ts.forEachChild(node, visit);
        }
        visit(body);
        return found;
    }
    for (const statement of sourceFile.statements) {
        if (ts.isVariableStatement(statement)
            && (statement.declarationList.flags & ts.NodeFlags.Const)) {
            for (const declaration of statement.declarationList.declarations) {
                if (!ts.isIdentifier(declaration.name) || !declaration.initializer) continue;
                const fn = unwrapExpression(declaration.initializer);
                if (!(ts.isArrowFunction(fn) || ts.isFunctionExpression(fn))
                    || !isAsync(fn) || !hasOwnAwait(fn.body)) continue;
                registerHelper(declaration.name);
            }
        } else if (ts.isFunctionDeclaration(statement) && statement.name
            && isAsync(statement) && statement.body && hasOwnAwait(statement.body)) {
            registerHelper(statement.name);
        }
    }
    return function collect(node, fileFeatures) {
        if (!ts.isExpressionStatement(node) || !ts.isBlock(node.parent)) return;
        const caller = node.parent.parent;
        if (!ts.isFunctionLike(caller) || caller.body !== node.parent || !isAsync(caller)) return;
        const call = unwrapExpression(node.expression);
        if (!ts.isCallExpression(call) || !ts.isIdentifier(call.expression)
            || call.questionDotToken) return;
        const matches = helpers.get(call.expression.text);
        if (matches?.length !== 1) return;
        const helper = matches[0];
        const symbol = symbolAt(helper);
        if (!symbol || symbol.declarations?.length !== 1
            || symbol !== symbolAt(call.expression)
            || !/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(helper.text)) return;
        const helperLine = sourceFile.getLineAndCharacterOfPosition(helper.getStart(sourceFile)).line + 1;
        const callLine = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
        if (helperLine < callLine) {
            fileFeatures.add(`TypeScript:UnawaitedAsyncHelperCall:${helper.text}:${helperLine}:${callLine}`);
        }
    };
}

function hasOnlyLogNamedCalls(block) {
    const logNames = new Set(['log', 'logError', 'error', 'warn', 'info', 'debug']);
    return ts.isBlock(block) && block.statements.length > 0
        && block.statements.every(statement => {
            if (!ts.isExpressionStatement(statement)) return false;
            const call = unwrapExpression(statement.expression);
            return ts.isCallExpression(call) && !call.questionDotToken
                && ts.isPropertyAccessExpression(call.expression)
                && logNames.has(call.expression.name.text);
        });
}

function createCaughtAsyncAwaitCollector(sourceFile, targetFile) {
    if (sourceFile.parseDiagnostics?.length || !/\.(?:ts|tsx|mts|cts)$/i.test(targetFile)) {
        return () => {};
    }
    function isAsync(fn) {
        return !!fn.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.AsyncKeyword);
    }
    function ownAwait(body) {
        let found = false;
        function visit(node) {
            if (found || ts.isFunctionLike(node)) return;
            if (ts.isAwaitExpression(node)) {
                found = true;
                return;
            }
            ts.forEachChild(node, visit);
        }
        visit(body);
        return found;
    }
    return function collect(node, fileFeatures) {
        if (!ts.isBlock(node) || !ts.isFunctionLike(node.parent)
            || node.parent.body !== node || !isAsync(node.parent)) return;
        let name = null;
        if (ts.isFunctionDeclaration(node.parent) && node.parent.name) {
            name = node.parent.name;
        } else if ((ts.isArrowFunction(node.parent) || ts.isFunctionExpression(node.parent))
            && ts.isVariableDeclaration(node.parent.parent)
            && ts.isIdentifier(node.parent.parent.name)) {
            name = node.parent.parent.name;
        }
        if (!name || !/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(name.text)) return;
        if (node.statements.length !== 1) return;
        for (const statement of node.statements) {
            if (!ts.isTryStatement(statement) || !statement.catchClause
                || statement.finallyBlock
                || !ownAwait(statement.tryBlock)
                || !hasOnlyLogNamedCalls(statement.catchClause.block)) continue;
            const helperLine = sourceFile.getLineAndCharacterOfPosition(name.getStart(sourceFile)).line + 1;
            const catchLine = sourceFile.getLineAndCharacterOfPosition(
                statement.catchClause.getStart(sourceFile)).line + 1;
            if (helperLine < catchLine) {
                fileFeatures.add(`TypeScript:CaughtAsyncAwaitNoRethrow:${name.text}:${helperLine}:${catchLine}`);
            }
        }
    };
}

function createReturnedQueueCatchCollector(sourceFile, targetFile, symbolAt) {
    if (sourceFile.parseDiagnostics?.length || !/\.(?:ts|tsx|mts|cts)$/i.test(targetFile)) {
        return () => {};
    }
    function isAsync(fn) {
        return !!fn.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.AsyncKeyword);
    }
    function ownAwait(body) {
        let found = false;
        function visit(node) {
            if (found || ts.isFunctionLike(node)) return;
            if (ts.isAwaitExpression(node)) {
                found = true;
                return;
            }
            ts.forEachChild(node, visit);
        }
        visit(body);
        return found;
    }
    function indexed(node) {
        const value = unwrapExpression(node);
        if (!ts.isElementAccessExpression(value) || value.questionDotToken) return null;
        const root = unwrapExpression(value.expression);
        const key = unwrapExpression(value.argumentExpression);
        return ts.isIdentifier(root) && ts.isIdentifier(key) ? {root, key} : null;
    }
    function sameIndexed(left, right) {
        return !!left && !!right && left.root.text === right.root.text
            && left.key.text === right.key.text
            && !!symbolAt(left.root) && symbolAt(left.root) === symbolAt(right.root)
            && !!symbolAt(left.key) && symbolAt(left.key) === symbolAt(right.key);
    }
    function callbackForThen(statements, thenName) {
        if (!ts.isIdentifier(thenName)) return null;
        const binding = symbolAt(thenName);
        if (!binding || binding.declarations?.length !== 1) return null;
        for (const statement of statements) {
            if (!ts.isVariableStatement(statement)
                || !(statement.declarationList.flags & ts.NodeFlags.Const)
                || statement.declarationList.declarations.length !== 1) continue;
            const declaration = statement.declarationList.declarations[0];
            if (!ts.isIdentifier(declaration.name) || declaration.name.text !== thenName.text
                || symbolAt(declaration.name) !== binding) continue;
            const fn = declaration.initializer && unwrapExpression(declaration.initializer);
            if (fn && (ts.isArrowFunction(fn) || ts.isFunctionExpression(fn))
                && isAsync(fn) && ts.isBlock(fn.body) && ownAwait(fn.body)) return fn;
        }
        return null;
    }
    function falsyLookupGuard(callback) {
        const statements = callback.body.statements;
        if (statements.length !== 2 || !ts.isVariableStatement(statements[0])
            || !(statements[0].declarationList.flags & ts.NodeFlags.Const)
            || statements[0].declarationList.declarations.length !== 1
            || !ts.isIfStatement(statements[1])) return null;
        const declaration = statements[0].declarationList.declarations[0];
        const awaited = declaration.initializer && unwrapExpression(declaration.initializer);
        const guard = statements[1];
        if (!ts.isIdentifier(declaration.name) || !awaited || !ts.isAwaitExpression(awaited)
            || !ts.isCallExpression(unwrapExpression(awaited.expression))
            || !ts.isIdentifier(unwrapExpression(guard.expression))
            || !symbolAt(declaration.name)
            || symbolAt(declaration.name) !== symbolAt(unwrapExpression(guard.expression))
            || !ts.isBlock(guard.thenStatement) || !ownAwait(guard.thenStatement)) return null;
        if (guard.elseStatement) {
            const alternative = guard.elseStatement;
            if (ts.isBlock(alternative)) {
                if (!hasOnlyLogNamedCalls(alternative)) return null;
            } else if (!ts.isIfStatement(alternative) || alternative.elseStatement
                || !hasOnlyLogNamedCalls(alternative.thenStatement)) return null;
        }
        return {lookup: declaration.name, guard};
    }
    return function collect(node, fileFeatures) {
        if (!ts.isBlock(node) || !ts.isFunctionLike(node.parent)
            || node.parent.body !== node || !isAsync(node.parent)
            || node.statements.length < 3) return;
        let name = null;
        if (ts.isFunctionDeclaration(node.parent) && node.parent.name) {
            name = node.parent.name;
        } else if ((ts.isArrowFunction(node.parent) || ts.isFunctionExpression(node.parent))
            && ts.isVariableDeclaration(node.parent.parent)
            && ts.isIdentifier(node.parent.parent.name)) {
            name = node.parent.parent.name;
        }
        if (!name || !/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(name.text)) return;
        const assignment = node.statements[node.statements.length - 2];
        const returned = node.statements[node.statements.length - 1];
        if (!ts.isExpressionStatement(assignment) || !ts.isReturnStatement(returned)
            || !returned.expression) return;
        const expression = unwrapExpression(assignment.expression);
        if (!ts.isBinaryExpression(expression)
            || expression.operatorToken.kind !== ts.SyntaxKind.EqualsToken
            || !sameIndexed(indexed(expression.left), indexed(returned.expression))) return;
        const caught = unwrapExpression(expression.right);
        if (!ts.isCallExpression(caught) || caught.questionDotToken
            || caught.arguments.length !== 1 || !ts.isPropertyAccessExpression(caught.expression)
            || caught.expression.name.text !== 'catch') return;
        const catchFn = unwrapExpression(caught.arguments[0]);
        if (!(ts.isArrowFunction(catchFn) || ts.isFunctionExpression(catchFn))
            || !hasOnlyLogNamedCalls(catchFn.body)) return;
        const chained = unwrapExpression(caught.expression.expression);
        if (!ts.isCallExpression(chained) || chained.questionDotToken
            || chained.arguments.length !== 1 || !ts.isPropertyAccessExpression(chained.expression)
            || chained.expression.name.text !== 'then') return;
        const callback = callbackForThen(node.statements.slice(0, -2),
            unwrapExpression(chained.arguments[0]));
        if (!callback) return;
        const assignmentLine = sourceFile.getLineAndCharacterOfPosition(assignment.getStart(sourceFile)).line + 1;
        const returnLine = sourceFile.getLineAndCharacterOfPosition(returned.getStart(sourceFile)).line + 1;
        if (assignmentLine < returnLine) {
            fileFeatures.add(`TypeScript:ReturnedQueueCatchNoRethrow:${name.text}:${assignmentLine}:${returnLine}`);
        }
        const falsy = falsyLookupGuard(callback);
        if (!falsy) return;
        const lookupLine = sourceFile.getLineAndCharacterOfPosition(falsy.lookup.getStart(sourceFile)).line + 1;
        const guardLine = sourceFile.getLineAndCharacterOfPosition(falsy.guard.getStart(sourceFile)).line + 1;
        if (lookupLine < guardLine) {
            fileFeatures.add(`TypeScript:QueuedLookupFalsyFallthrough:${name.text}:${lookupLine}:${guardLine}`);
        }
    };
}

function createRepositoryBulkDirectWriteCollector(sourceFile, targetFile, importBindings, symbolAt) {
    if (sourceFile.parseDiagnostics?.length || !/\.(?:ts|tsx|mts|cts)$/i.test(targetFile)) {
        return () => {};
    }
    const bulkMethods = new Set(['bulkCreate', 'bulkUpdate']);
    const storageWrites = new Set(['bulkAdd', 'bulkPut']);
    function importedRoot(expression) {
        let root = unwrapExpression(expression);
        while (ts.isPropertyAccessExpression(root)) root = unwrapExpression(root.expression);
        if (!ts.isIdentifier(root) || !importBindings.has(root.text)) return false;
        const declarations = symbolAt(root)?.declarations || [];
        const binding = declarations.length === 1 ? declarations[0] : null;
        if (binding && ts.isImportSpecifier(binding) && binding.isTypeOnly) return false;
        let declaration = binding;
        while (declaration && !ts.isImportDeclaration(declaration)) declaration = declaration.parent;
        return !!declaration && !declaration.importClause?.isTypeOnly
            && declaration.moduleSpecifier?.text === importBindings.get(root.text).source;
    }
    function directStorageWrite(call) {
        if (!ts.isCallExpression(call) || call.questionDotToken
            || !ts.isPropertyAccessExpression(call.expression)
            || !storageWrites.has(call.expression.name.text)) return false;
        const receiver = unwrapExpression(call.expression.expression);
        return (ts.isPropertyAccessExpression(receiver)
            && receiver.expression.kind === ts.SyntaxKind.ThisKeyword
            && receiver.name.text === 'table')
            || importedRoot(receiver);
    }
    function permissiveLocalSchema(cls) {
        const ctor = cls.members.find(member => ts.isConstructorDeclaration(member));
        const superCall = ctor?.body?.statements.find(statement =>
            ts.isExpressionStatement(statement) && ts.isCallExpression(statement.expression)
            && statement.expression.expression.kind === ts.SyntaxKind.SuperKeyword
        )?.expression;
        const schema = superCall?.arguments[1];
        if (!schema || !ts.isIdentifier(schema)) return false;
        const binding = symbolAt(schema);
        if (!binding || binding.declarations?.length !== 1) return false;
        const declaration = binding.declarations[0];
        if (!ts.isVariableDeclaration(declaration) || declaration.parent.parent.parent !== sourceFile) return false;
        const initializer = declaration.initializer && unwrapExpression(declaration.initializer);
        if (!initializer || !ts.isCallExpression(initializer) || initializer.arguments.length
            || !ts.isPropertyAccessExpression(initializer.expression)
            || !['any', 'unknown'].includes(initializer.expression.name.text)) return false;
        const z = unwrapExpression(initializer.expression.expression);
        return ts.isIdentifier(z) && importBindings.get(z.text)?.source === 'zod'
            && importedRoot(z);
    }
    function priorBaseOrValidationCall(method, writeStart) {
        const collection = method.parameters[0]?.name;
        const collectionSymbol = collection && ts.isIdentifier(collection) && symbolAt(collection);
        for (const statement of method.body.statements) {
            if (statement.getStart(sourceFile) >= writeStart || !ts.isExpressionStatement(statement)) continue;
            const expression = unwrapExpression(statement.expression);
            const call = ts.isAwaitExpression(expression)
                ? unwrapExpression(expression.expression) : expression;
            if (!ts.isCallExpression(call) || !ts.isPropertyAccessExpression(call.expression)) continue;
            const owner = unwrapExpression(call.expression.expression);
            if (ts.isAwaitExpression(expression) && owner.kind === ts.SyntaxKind.SuperKeyword
                && call.expression.name.text === method.name.text) return true;
            if (!collectionSymbol || !ts.isIdentifier(owner)
                || symbolAt(owner) !== collectionSymbol
                || call.expression.name.text !== 'forEach'
                || call.arguments.length !== 1) continue;
            const callback = unwrapExpression(call.arguments[0]);
            if (!(ts.isArrowFunction(callback) || ts.isFunctionExpression(callback))
                || callback.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.AsyncKeyword)
                || callback.parameters.length !== 1 || !ts.isIdentifier(callback.parameters[0].name)) continue;
            const item = callback.parameters[0].name;
            const body = ts.isBlock(callback.body) && callback.body.statements.length === 1
                && ts.isExpressionStatement(callback.body.statements[0])
                ? callback.body.statements[0].expression : callback.body;
            const validation = unwrapExpression(body);
            if (!ts.isCallExpression(validation) || validation.arguments.length !== 1
                || !ts.isPropertyAccessExpression(validation.expression)
                || validation.expression.expression.kind !== ts.SyntaxKind.ThisKeyword
                || validation.expression.name.text !== 'validateForWrite') continue;
            const validated = unwrapExpression(validation.arguments[0]);
            if (ts.isIdentifier(validated) && symbolAt(validated) === symbolAt(item)) return true;
        }
        return false;
    }
    return function collect(node, fileFeatures) {
        if (!ts.isClassDeclaration(node) || !node.name || !/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(node.name.text)) return;
        const parentClass = node.heritageClauses?.find(clause =>
            clause.token === ts.SyntaxKind.ExtendsKeyword)?.types[0]?.expression;
        if (!parentClass || !ts.isIdentifier(parentClass) || !/Repository$/.test(parentClass.text)
            || !importedRoot(parentClass) || permissiveLocalSchema(node)) return;
        for (const method of node.members) {
            if (!ts.isMethodDeclaration(method) || !ts.isIdentifier(method.name)
                || !bulkMethods.has(method.name.text) || !method.body) continue;
            let firstWrite = null;
            function visit(inner) {
                if (inner !== method.body && ts.isFunctionLike(inner)) return;
                if (ts.isAwaitExpression(inner) && directStorageWrite(unwrapExpression(inner.expression))
                    && (!firstWrite || inner.getStart(sourceFile) < firstWrite.getStart(sourceFile))) {
                    firstWrite = inner;
                }
                ts.forEachChild(inner, visit);
            }
            visit(method.body);
            if (!firstWrite || priorBaseOrValidationCall(method, firstWrite.getStart(sourceFile))) continue;
            const methodLine = sourceFile.getLineAndCharacterOfPosition(method.name.getStart(sourceFile)).line + 1;
            const writeLine = sourceFile.getLineAndCharacterOfPosition(firstWrite.getStart(sourceFile)).line + 1;
            if (methodLine < writeLine) {
                fileFeatures.add(`TypeScript:RepositoryBulkDirectWrite:${node.name.text}:${method.name.text}:${methodLine}:${writeLine}`);
            }
        }
    };
}

function createFailedSafeParseRawFallbackCollector(sourceFile, targetFile, symbolAt) {
    if (sourceFile.parseDiagnostics?.length || !/\.(?:ts|tsx|mts|cts)$/i.test(targetFile)) {
        return () => {};
    }
    function sameIdentifier(left, right) {
        return ts.isIdentifier(left) && ts.isIdentifier(right)
            && !!symbolAt(left) && symbolAt(left) === symbolAt(right);
    }
    function isConsoleLog(statement) {
        if (!ts.isExpressionStatement(statement)) return false;
        const call = unwrapExpression(statement.expression);
        return ts.isCallExpression(call) && !call.questionDotToken
            && ts.isPropertyAccessExpression(call.expression)
            && ts.isIdentifier(call.expression.expression)
            && call.expression.expression.text === 'console'
            && ['error', 'warn', 'log', 'info'].includes(call.expression.name.text)
            && !symbolAt(call.expression.expression);
    }
    return function collect(node, fileFeatures) {
        if (!ts.isClassDeclaration(node) || !node.name
            || !/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(node.name.text)) return;
        for (const method of node.members) {
            if (!ts.isMethodDeclaration(method) || !ts.isIdentifier(method.name)
                || !/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(method.name.text)
                || method.body?.statements.length !== 3) continue;
            const [declaration, guard, successReturn] = method.body.statements;
            if (!ts.isVariableStatement(declaration)
                || !(declaration.declarationList.flags & ts.NodeFlags.Const)
                || declaration.declarationList.declarations.length !== 1
                || !ts.isIfStatement(guard) || guard.elseStatement
                || !ts.isReturnStatement(successReturn) || !successReturn.expression) continue;
            const parsed = declaration.declarationList.declarations[0];
            const call = parsed.initializer && unwrapExpression(parsed.initializer);
            if (!ts.isIdentifier(parsed.name) || !call || !ts.isCallExpression(call)
                || call.questionDotToken || call.arguments.length !== 1
                || !ts.isPropertyAccessExpression(call.expression)
                || call.expression.name.text !== 'safeParse') continue;
            const schema = unwrapExpression(call.expression.expression);
            const raw = unwrapExpression(call.arguments[0]);
            if (!ts.isPropertyAccessExpression(schema)
                || schema.expression.kind !== ts.SyntaxKind.ThisKeyword
                || !ts.isIdentifier(raw) || !method.parameters.some(parameter =>
                    ts.isIdentifier(parameter.name) && sameIdentifier(parameter.name, raw))) continue;
            const failure = unwrapExpression(guard.expression);
            if (!ts.isPrefixUnaryExpression(failure)
                || failure.operator !== ts.SyntaxKind.ExclamationToken) continue;
            const success = unwrapExpression(failure.operand);
            if (!ts.isPropertyAccessExpression(success) || success.name.text !== 'success'
                || !sameIdentifier(unwrapExpression(success.expression), parsed.name)) continue;
            const failedStatements = ts.isBlock(guard.thenStatement)
                ? guard.thenStatement.statements : [guard.thenStatement];
            const failedReturn = failedStatements[failedStatements.length - 1];
            if (!ts.isReturnStatement(failedReturn) || !failedReturn.expression
                || !failedStatements.slice(0, -1).every(isConsoleLog)
                || !sameIdentifier(unwrapExpression(failedReturn.expression), raw)) continue;
            const validated = unwrapExpression(successReturn.expression);
            if (!ts.isPropertyAccessExpression(validated) || validated.name.text !== 'data'
                || !sameIdentifier(unwrapExpression(validated.expression), parsed.name)) continue;
            const guardLine = sourceFile.getLineAndCharacterOfPosition(guard.getStart(sourceFile)).line + 1;
            const returnLine = sourceFile.getLineAndCharacterOfPosition(failedReturn.getStart(sourceFile)).line + 1;
            if (guardLine < returnLine) {
                fileFeatures.add('TypeScript:FailedSafeParseReturnsRaw:' + node.name.text + ':' + method.name.text + ':' + guardLine + ':' + returnLine);
            }
        }
    };
}

function createRepositoryDirectReadReturnCollector(sourceFile, targetFile, importBindings, symbolAt) {
    if (sourceFile.parseDiagnostics?.length || !/\.(?:ts|tsx|mts|cts)$/i.test(targetFile)) {
        return () => {};
    }
    function boundImport(identifier) {
        if (!ts.isIdentifier(identifier)) return null;
        const binding = importBindings.get(identifier.text);
        const declarations = symbolAt(identifier)?.declarations || [];
        const declaration = declarations.length === 1 ? declarations[0] : null;
        if (!binding || !declaration) return null;
        let importNode = declaration;
        while (importNode && !ts.isImportDeclaration(importNode)) importNode = importNode.parent;
        if (!importNode || importNode.importClause?.isTypeOnly
            || (ts.isImportSpecifier(declaration) && declaration.isTypeOnly)
            || importNode.moduleSpecifier?.text !== binding.source) return null;
        return binding;
    }
    function selectionOnlyPredicate(node) {
        const callback = unwrapExpression(node);
        if (!ts.isArrowFunction(callback) || callback.parameters.length !== 1
            || callback.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.AsyncKeyword)
            || ts.isBlock(callback.body)) return false;
        const parameter = callback.parameters[0];
        if (!ts.isIdentifier(parameter.name) || parameter.initializer || parameter.dotDotDotToken) return false;
        const binding = symbolAt(parameter.name);
        if (!binding) return false;
        function selectionValue(expression) {
            const value = unwrapExpression(expression);
            if (ts.isIdentifier(value)) return symbolAt(value) === binding;
            if (ts.isPropertyAccessExpression(value) && !value.questionDotToken) {
                return selectionValue(value.expression);
            }
            if (ts.isStringLiteral(value) || ts.isNumericLiteral(value)
                || [ts.SyntaxKind.TrueKeyword, ts.SyntaxKind.FalseKeyword, ts.SyntaxKind.NullKeyword].includes(value.kind)) return true;
            if (ts.isPrefixUnaryExpression(value) && value.operator === ts.SyntaxKind.ExclamationToken) {
                return selectionValue(value.operand);
            }
            if (ts.isBinaryExpression(value)
                && [ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.EqualsEqualsEqualsToken,
                    ts.SyntaxKind.ExclamationEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken,
                    ts.SyntaxKind.LessThanToken, ts.SyntaxKind.LessThanEqualsToken,
                    ts.SyntaxKind.GreaterThanToken, ts.SyntaxKind.GreaterThanEqualsToken,
                    ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken].includes(value.operatorToken.kind)) {
                return selectionValue(value.left) && selectionValue(value.right);
            }
            return false;
        }
        return selectionValue(callback.body);
    }
    function collectionRead(node) {
        const awaited = unwrapExpression(node);
        if (!ts.isAwaitExpression(awaited)) return null;
        const call = unwrapExpression(awaited.expression);
        if (!ts.isCallExpression(call) || call.questionDotToken || call.arguments.length
            || !ts.isPropertyAccessExpression(call.expression) || call.expression.questionDotToken
            || call.expression.name.text !== 'toArray') return null;
        let receiver = unwrapExpression(call.expression.expression);
        // Only a small source grammar of selection chains, not arbitrary builders.
        while (ts.isCallExpression(receiver)) {
            const query = receiver;
            if (query.questionDotToken || query.arguments.length !== 1
                || !ts.isPropertyAccessExpression(query.expression) || query.expression.questionDotToken) return null;
            const operation = query.expression.name.text;
            if (operation === 'filter') {
                if (!selectionOnlyPredicate(query.arguments[0])) return null;
            } else if (operation === 'where') {
                const key = unwrapExpression(query.arguments[0]);
                if (!ts.isStringLiteral(key)) return null;
            } else if (operation !== 'equals') return null;
            receiver = unwrapExpression(query.expression.expression);
        }
        if (!ts.isPropertyAccessExpression(receiver) || receiver.questionDotToken) return null;
        if (receiver.expression.kind === ts.SyntaxKind.ThisKeyword && receiver.name.text === 'table') return awaited;
        const binding = boundImport(unwrapExpression(receiver.expression));
        return binding && /(?:^|\/)(?:db|database|dexie(?:db)?)(?:\/|$)/i.test(binding.source) ? awaited : null;
    }
    function collectCollectionReturn(method, className, fileFeatures) {
        if (!/^getAll(?:By[A-Z][A-Za-z0-9_$]*)?$/.test(method.name.text)
            || !method.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.AsyncKeyword)) return;
        const statements = method.body.statements;
        let read = null;
        let returned = null;
        let form = null;
        if (statements.length === 1 && ts.isReturnStatement(statements[0]) && statements[0].expression) {
            if (!collectionRead(statements[0].expression)) return;
            read = method.name;
            returned = statements[0];
            form = 'inline_await';
        } else if (statements.length === 2 && ts.isVariableStatement(statements[0])
            && (statements[0].declarationList.flags & ts.NodeFlags.Const)
            && statements[0].declarationList.declarations.length === 1
            && ts.isReturnStatement(statements[1]) && statements[1].expression) {
            const declaration = statements[0].declarationList.declarations[0];
            if (!ts.isIdentifier(declaration.name) || !declaration.initializer) return;
            read = collectionRead(declaration.initializer);
            const binding = read && symbolAt(declaration.name);
            if (!binding) return;
            const value = unwrapExpression(statements[1].expression);
            if (ts.isIdentifier(value) && symbolAt(value) === binding) {
                form = 'const_binding';
            } else if (ts.isCallExpression(value) && !value.questionDotToken && value.arguments.length === 1
                && ts.isPropertyAccessExpression(value.expression) && !value.expression.questionDotToken
                && value.expression.name.text === 'filter'
                && ts.isIdentifier(unwrapExpression(value.expression.expression))
                && symbolAt(unwrapExpression(value.expression.expression)) === binding
                && selectionOnlyPredicate(value.arguments[0])) {
                form = 'filtered_binding';
            } else return;
            returned = statements[1];
        } else return;
        const readLine = sourceFile.getLineAndCharacterOfPosition(read.getStart(sourceFile)).line + 1;
        const returnLine = sourceFile.getLineAndCharacterOfPosition(returned.getStart(sourceFile)).line + 1;
        if (readLine < returnLine) {
            fileFeatures.add('TypeScript:RepositoryCollectionReadReturn:' + className
                + ':' + method.name.text + ':' + form + ':' + readLine + ':' + returnLine);
        }
    }
    function directRead(node) {
        const awaited = unwrapExpression(node);
        if (!ts.isAwaitExpression(awaited)) return null;
        const call = unwrapExpression(awaited.expression);
        if (!ts.isCallExpression(call) || call.questionDotToken || call.arguments.length !== 1
            || !ts.isPropertyAccessExpression(call.expression)
            || call.expression.questionDotToken || call.expression.name.text !== 'get') return null;
        const receiver = unwrapExpression(call.expression.expression);
        if (ts.isPropertyAccessExpression(receiver) && receiver.questionDotToken) return null;
        if (ts.isPropertyAccessExpression(receiver)
            && receiver.expression.kind === ts.SyntaxKind.ThisKeyword
            && receiver.name.text === 'table') return awaited;
        if (!ts.isPropertyAccessExpression(receiver)) return null;
        const root = unwrapExpression(receiver.expression);
        const binding = boundImport(root);
        return binding && /(?:^|\/)(?:db|database|dexie(?:db)?)(?:\/|$)/i.test(binding.source)
            ? awaited : null;
    }
    function methodHasReadValidation(method) {
        let found = false;
        function visit(node) {
            if (found || (node !== method.body && ts.isFunctionLike(node))) return;
            if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
                const owner = unwrapExpression(node.expression.expression);
                if ((owner.kind === ts.SyntaxKind.ThisKeyword
                    && node.expression.name.text === 'validateReadData')
                    || (owner.kind === ts.SyntaxKind.SuperKeyword
                        && node.expression.name.text === 'getById')) {
                    found = true;
                    return;
                }
            }
            ts.forEachChild(node, visit);
        }
        visit(method.body);
        return found;
    }
    function parsesReadBinding(method, binding) {
        let found = false;
        function visit(node) {
            if (found || (node !== method.body && ts.isFunctionLike(node))) return;
            if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
                && ['parse', 'safeParse'].includes(node.expression.name.text)
                && node.arguments.some(argument => {
                    const value = unwrapExpression(argument);
                    return ts.isIdentifier(value) && symbolAt(value) === binding;
                })) {
                found = true;
                return;
            }
            ts.forEachChild(node, visit);
        }
        visit(method.body);
        return found;
    }
    return function collect(node, fileFeatures) {
        if (!ts.isClassDeclaration(node) || !node.name
            || !/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(node.name.text)) return;
        const parent = node.heritageClauses?.find(clause =>
            clause.token === ts.SyntaxKind.ExtendsKeyword)?.types[0]?.expression;
        if (!parent || !ts.isIdentifier(parent)
            || !/Repository$/.test(parent.text) || !boundImport(parent)) return;
        for (const method of node.members) {
            if (ts.isMethodDeclaration(method) && ts.isIdentifier(method.name) && method.body) {
                collectCollectionReturn(method, node.name.text, fileFeatures);
            }
            if (!ts.isMethodDeclaration(method) || !ts.isIdentifier(method.name)
                || method.name.text !== 'getById' || !method.body
                || methodHasReadValidation(method)) continue;
            // Inline return is a distinct source form; the declaration line is
            // context, not an executed guard or a resolved storage type.
            if (method.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.AsyncKeyword)) {
                for (const statement of method.body.statements) {
                    if (!ts.isReturnStatement(statement) || !statement.expression
                        || !directRead(statement.expression)) continue;
                    const methodLine = sourceFile.getLineAndCharacterOfPosition(method.name.getStart(sourceFile)).line + 1;
                    const returnLine = sourceFile.getLineAndCharacterOfPosition(statement.getStart(sourceFile)).line + 1;
                    if (methodLine < returnLine) {
                        fileFeatures.add('TypeScript:RepositoryInlineReadReturn:' + node.name.text
                            + ':getById:' + methodLine + ':' + returnLine);
                    }
                }
            }
            for (const statement of method.body.statements) {
                if (!ts.isVariableStatement(statement)
                    || !(statement.declarationList.flags & ts.NodeFlags.Const)) continue;
                for (const declaration of statement.declarationList.declarations) {
                    if (!ts.isIdentifier(declaration.name) || !declaration.initializer) continue;
                    const read = directRead(declaration.initializer);
                    const binding = read && symbolAt(declaration.name);
                    if (!binding || parsesReadBinding(method, binding)) continue;
                    let returned = null;
                    function findReturn(inner) {
                        if (returned || (inner !== method.body && ts.isFunctionLike(inner))) return;
                        if (ts.isReturnStatement(inner) && inner.expression) {
                            const value = unwrapExpression(inner.expression);
                            if (ts.isIdentifier(value) && symbolAt(value) === binding) {
                                returned = inner;
                                return;
                            }
                        }
                        ts.forEachChild(inner, findReturn);
                    }
                    findReturn(method.body);
                    if (!returned) continue;
                    const readLine = sourceFile.getLineAndCharacterOfPosition(read.getStart(sourceFile)).line + 1;
                    const returnLine = sourceFile.getLineAndCharacterOfPosition(returned.getStart(sourceFile)).line + 1;
                    if (readLine < returnLine) {
                        fileFeatures.add('TypeScript:RepositoryDirectReadReturn:' + node.name.text
                            + ':getById:' + readLine + ':' + returnLine);
                    }
                }
            }
        }
    };
}

function createImmutableKeyTruthinessGuardCollector(sourceFile, targetFile) {
    if (sourceFile.parseDiagnostics?.length || !/\.(?:ts|tsx|mts|cts)$/i.test(targetFile)) {
        return () => {};
    }

    function member(node) {
        const value = node ? unwrapExpression(node) : null;
        if (!value || !ts.isPropertyAccessExpression(value) || value.questionDotToken) return null;
        const root = unwrapExpression(value.expression);
        return ts.isIdentifier(root) ? {root: root.text, key: value.name.text} : null;
    }

    function sameMember(left, right) {
        return Boolean(left && right && left.root === right.root && left.key === right.key);
    }

    function throwsDirectly(statement) {
        if (ts.isThrowStatement(statement)) return true;
        return ts.isBlock(statement)
            && statement.statements.length === 1
            && ts.isThrowStatement(statement.statements[0]);
    }

    function guardMembers(statement) {
        if (!ts.isIfStatement(statement) || statement.elseStatement
            || !throwsDirectly(statement.thenStatement)) return null;
        const condition = unwrapExpression(statement.expression);
        if (!ts.isBinaryExpression(condition)
            || condition.operatorToken.kind !== ts.SyntaxKind.AmpersandAmpersandToken) return null;
        const truthy = member(condition.left);
        const comparison = unwrapExpression(condition.right);
        if (!truthy || !ts.isBinaryExpression(comparison)
            || comparison.operatorToken.kind !== ts.SyntaxKind.ExclamationEqualsEqualsToken) return null;
        const left = member(comparison.left);
        const right = member(comparison.right);
        if (sameMember(truthy, left) && right && right.root !== truthy.root
            && right.key === truthy.key) return {patch: truthy.root, original: right.root, key: truthy.key};
        if (sameMember(truthy, right) && left && left.root !== truthy.root
            && left.key === truthy.key) return {patch: truthy.root, original: left.root, key: truthy.key};
        return null;
    }

    function returnsOverwritingSpread(statement, guard) {
        if (!ts.isReturnStatement(statement) || !statement.expression) return false;
        const result = unwrapExpression(statement.expression);
        if (!ts.isObjectLiteralExpression(result) || result.properties.length !== 2) return false;
        const [original, patch] = result.properties;
        return ts.isSpreadAssignment(original) && ts.isSpreadAssignment(patch)
            && ts.isIdentifier(unwrapExpression(original.expression))
            && ts.isIdentifier(unwrapExpression(patch.expression))
            && unwrapExpression(original.expression).text === guard.original
            && unwrapExpression(patch.expression).text === guard.patch;
    }

    function hasPriorParserUse(statements, beforeIndex, patchName) {
        let found = false;
        function visit(node) {
            if (found) return;
            if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)
                && node.name.text === patchName && node.initializer) {
                const initializer = unwrapExpression(node.initializer);
                if (ts.isCallExpression(initializer)
                    && ts.isPropertyAccessExpression(initializer.expression)
                    && initializer.expression.name.text === 'parse') {
                    found = true;
                    return;
                }
            }
            if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
                && ['parse', 'safeParse'].includes(node.expression.name.text)
                && node.arguments.some(argument => {
                    const value = unwrapExpression(argument);
                    return ts.isIdentifier(value) && value.text === patchName;
                })) {
                found = true;
                return;
            }
            ts.forEachChild(node, visit);
        }
        for (const statement of statements.slice(0, beforeIndex)) visit(statement);
        return found;
    }

    return function collect(node, fileFeatures) {
        if (!ts.isBlock(node)) return;
        const statements = node.statements;
        for (let index = 0; index + 1 < statements.length; index++) {
            const guard = guardMembers(statements[index]);
            if (!guard || !returnsOverwritingSpread(statements[index + 1], guard)
                || hasPriorParserUse(statements, index, guard.patch)) continue;
            const guardLine = sourceFile.getLineAndCharacterOfPosition(
                statements[index].getStart(sourceFile)
            ).line + 1;
            const sinkLine = sourceFile.getLineAndCharacterOfPosition(
                statements[index + 1].getStart(sourceFile)
            ).line + 1;
            if (guardLine < sinkLine) {
                fileFeatures.add(`TypeScript:ImmutableKeyTruthinessGuard:${guard.key}:${guardLine}:${sinkLine}`);
            }
        }
    };
}

function detectRuntimeExportContract(targetFile, fileFeatures, symbolName) {
    const normalizedPath = (targetFile || '').replace(/\\/g, '/').toLowerCase();
    const fileName = normalizedPath.split('/').pop() || '';
    const sym = String(symbolName || '').trim();
    const symLower = sym.toLowerCase();

    const routeModuleSymbols = new Set([
        'loader', 'action', 'meta', 'links', 'headers', 'handle', 'errorboundary', 'layout',
        'get', 'post', 'put', 'patch', 'delete', 'head', 'options',
    ]);
    const nextPagesRuntimeSymbols = new Set([
        'getserversideprops', 'getstaticprops', 'getstaticpaths', 'config', 'reportwebvitals',
    ]);
    const nextAppRuntimeSymbols = new Set([
        'metadata', 'generatemetadata', 'viewport', 'generateviewport', 'generatestaticparams',
        'dynamic', 'dynamicparams', 'revalidate', 'fetchcache', 'runtime', 'preferredregion', 'maxduration',
    ]);

    if (fileName.includes('.stories.') || fileName.includes('.story.')) {
        if (symLower === 'default' || symLower === 'meta' || /story|stories/.test(symLower)) {
            return { matched: true, kind: 'storybook' };
        }
    }

    const hasRouterFeature = ['RouterConfig', 'RouteLoader', 'RouteAction', 'RouteLazy'].some(feature => fileFeatures.has(feature));
    const routeLikePath =
        normalizedPath.startsWith('routes/') ||
        normalizedPath.includes('/routes/') ||
        normalizedPath.endsWith('/route.ts') ||
        normalizedPath.endsWith('/route.tsx') ||
        normalizedPath.endsWith('/route.js') ||
        normalizedPath.endsWith('/route.jsx');
    if ((hasRouterFeature || routeLikePath) && routeModuleSymbols.has(symLower)) {
        return { matched: true, kind: 'react_router' };
    }

    const isNextPagesPath = normalizedPath.startsWith('pages/') || normalizedPath.includes('/pages/');
    if (isNextPagesPath && nextPagesRuntimeSymbols.has(symLower)) {
        return { matched: true, kind: 'next_pages_runtime' };
    }

    const isNextAppPath = normalizedPath.startsWith('app/') || normalizedPath.includes('/app/');
    const nextAppSegmentFiles = [
        'page.tsx', 'page.ts', 'layout.tsx', 'layout.ts', 'template.tsx', 'template.ts',
        'page.jsx', 'page.js', 'layout.jsx', 'layout.js', 'template.jsx', 'template.js',
        'default.tsx', 'default.ts', 'default.jsx', 'default.js',
        'loading.tsx', 'loading.ts', 'loading.jsx', 'loading.js',
        'error.tsx', 'error.ts', 'error.jsx', 'error.js',
        'not-found.tsx', 'not-found.ts', 'not-found.jsx', 'not-found.js',
        'route.tsx', 'route.ts', 'route.jsx', 'route.js',
        'root.tsx', 'root.ts', 'root.jsx', 'root.js',
    ].includes(fileName);
    if (isNextAppPath && nextAppSegmentFiles && nextAppRuntimeSymbols.has(symLower)) {
        return { matched: true, kind: 'next_app_runtime' };
    }

    if ((fileName === 'middleware.ts' || fileName === 'middleware.js') && (symLower === 'middleware' || symLower === 'config')) {
        return { matched: true, kind: 'next_middleware' };
    }
    if ((fileName === 'instrumentation.ts' || fileName === 'instrumentation.js') && (symLower === 'register' || symLower === 'onrequesterror')) {
        return { matched: true, kind: 'next_instrumentation' };
    }
    if (
        (fileName === 'instrumentation-client.ts' || fileName === 'instrumentation-client.js')
        && symLower === 'onroutertransitionstart'
    ) {
        return { matched: true, kind: 'next_instrumentation_client' };
    }

    return { matched: false, kind: '' };
}

function detectGraphQLExportContract(sourceFile, sourceCode, targetFile, symbolName, declarationText = '') {
    const normalizedPath = (targetFile || '').replace(/\\/g, '/').toLowerCase();
    const fileName = normalizedPath.split('/').pop() || '';
    const sym = String(symbolName || '').trim();
    const symLower = sym.toLowerCase();
    if (!sym) return { matched: false, kind: '' };

    const graphqlFileHint =
        normalizedPath.includes('/graphql/') ||
        normalizedPath.includes('/fragments/') ||
        /fragment|query|mutation|subscription/.test(fileName);

    const graphqlContentHint =
        sourceCode.includes('gql`') ||
        sourceCode.includes('graphql`') ||
        sourceCode.includes('TypedDocumentNode') ||
        sourceCode.includes('DocumentNode');
    const decl = String(declarationText || '');
    const graphqlDeclHint =
        decl.includes('gql`') ||
        decl.includes('graphql`') ||
        decl.includes('TypedDocumentNode') ||
        decl.includes('DocumentNode');

    if (!(graphqlFileHint || graphqlContentHint || graphqlDeclHint)) {
        return { matched: false, kind: '' };
    }

    const contractPattern =
        /fragment|fragmentdoc|document|query|mutation|subscription/.test(symLower);
    // Dedicated fragments folders typically expose only GraphQL contract symbols,
    // even if individual symbol names do not include query/mutation suffixes.
    if (normalizedPath.includes('/fragments/') && (graphqlContentHint || graphqlDeclHint)) {
        return { matched: true, kind: 'graphql_document' };
    }
    if (graphqlDeclHint) {
        return { matched: true, kind: 'graphql_document' };
    }
    if (!contractPattern) {
        return { matched: false, kind: '' };
    }

    return { matched: true, kind: 'graphql_document' };
}

function collectModuleImportEvidence(sourceFile) {
    const records = [];
    const seen = new Set();

    function add(source, name, kind, scope = 'top_level') {
        const normalizedSource = String(source || '').trim();
        if (!normalizedSource) return;
        const record = {
            source: normalizedSource,
            name: String(name || '*').trim() || '*',
            kind: String(kind || 'module').trim() || 'module',
            scope,
        };
        const key = `${record.source}\u0000${record.name}\u0000${record.kind}\u0000${record.scope}`;
        if (!seen.has(key)) {
            seen.add(key);
            records.push(record);
        }
    }

    function moduleSource(node) {
        return node && ts.isStringLiteralLike(node) ? node.text : '';
    }

    function scopeFor(node) {
        let current = node.parent;
        while (current && !ts.isSourceFile(current)) {
            if (ts.isFunctionLike(current)) return 'local';
            current = current.parent;
        }
        return 'top_level';
    }

    function addDynamicMembers(importCall, source, scope) {
        let valueNode = importCall;
        if (ts.isAwaitExpression(valueNode.parent)) valueNode = valueNode.parent;
        if (ts.isVariableDeclaration(valueNode.parent) && valueNode.parent.initializer === valueNode) {
            const binding = valueNode.parent.name;
            if (ts.isObjectBindingPattern(binding)) {
                for (const element of binding.elements) {
                    const importedName = element.propertyName?.getText(sourceFile) || element.name.getText(sourceFile);
                    add(source, importedName, 'dynamic_member', scope);
                }
            }
        }

        const access = importCall.parent;
        const thenCall = access && ts.isPropertyAccessExpression(access) && access.name.text === 'then'
            ? access.parent
            : null;
        if (thenCall && ts.isCallExpression(thenCall)) {
            const callback = thenCall.arguments[0];
            const binding = callback && ts.isFunctionLike(callback) ? callback.parameters[0]?.name : null;
            if (binding && ts.isObjectBindingPattern(binding)) {
                for (const element of binding.elements) {
                    const importedName = element.propertyName?.getText(sourceFile) || element.name.getText(sourceFile);
                    add(source, importedName, 'dynamic_member', scopeFor(thenCall));
                }
            }
        }
    }

    function visit(node) {
        if (ts.isImportDeclaration(node)) {
            const source = moduleSource(node.moduleSpecifier);
            const clause = node.importClause;
            if (!clause) {
                add(source, '*', 'side_effect');
            } else {
                const clauseIsTypeOnly = Boolean(clause.isTypeOnly);
                if (clause.name) {
                    add(source, 'default', clauseIsTypeOnly ? 'type' : 'default');
                }
                const bindings = clause.namedBindings;
                if (bindings && ts.isNamespaceImport(bindings)) {
                    add(source, bindings.name.text, clauseIsTypeOnly ? 'type' : 'namespace');
                } else if (bindings && ts.isNamedImports(bindings)) {
                    for (const element of bindings.elements) {
                        const importedName = element.propertyName?.text || element.name.text;
                        add(source, importedName, clauseIsTypeOnly || element.isTypeOnly ? 'type' : 'named');
                    }
                }
            }
        } else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
            add(moduleSource(node.moduleReference.expression), node.name.text, node.isTypeOnly ? 'type' : 'require');
        } else if (ts.isExportDeclaration(node)) {
            const source = moduleSource(node.moduleSpecifier);
            if (source) {
                if (!node.exportClause) {
                    add(source, '*', node.isTypeOnly ? 'type' : 'reexport_all');
                } else if (ts.isNamespaceExport(node.exportClause)) {
                    add(source, node.exportClause.name.text, node.isTypeOnly ? 'type' : 'reexport_namespace');
                } else if (ts.isNamedExports(node.exportClause)) {
                    for (const element of node.exportClause.elements) {
                        const importedName = element.propertyName?.text || element.name.text;
                        add(source, importedName, node.isTypeOnly || element.isTypeOnly ? 'type' : 'reexport');
                    }
                }
            }
        } else if (ts.isCallExpression(node)) {
            const source = moduleSource(node.arguments[0]);
            if (source && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
                const scope = scopeFor(node);
                add(source, '*', 'dynamic', scope);
                addDynamicMembers(node, source, scope);
            } else if (source && ts.isIdentifier(node.expression) && node.expression.text === 'require') {
                add(source, '*', 'require', scopeFor(node));
            }
        }
        ts.forEachChild(node, visit);
    }

    visit(sourceFile);
    return records;
}

function analyzeFile(targetFile) {
    let sourceCode;
    try {
        sourceCode = fs.readFileSync(targetFile, 'utf8');
    } catch (err) {
        let mtime = 0;
        try {
            mtime = fs.statSync(targetFile).mtimeMs / 1000;
        } catch (_) {}
        return [{
            name: '__file_meta__',
            type: 'Meta',
            start: 0,
            end: 0,
            signature: 'File Level Genomic Data',
            dna: '',
            dependencies: [],
            parserStatus: 'unavailable',
            parserKind: 'typescript_compiler_api',
            semanticDepth: 'unavailable',
            parserDiagnosticCount: 0,
            moduleImports: [],
            directImportBindings: [],
            features: [
                'Error:FileReadFailed',
                'ParserStatus:unavailable',
                'LOC:0',
                `MTime:${mtime}`
            ]
        }];
    }
    const sourceFile = ts.createSourceFile(targetFile, sourceCode, ts.ScriptTarget.Latest, true);
    const parseDiagnostics = Array.isArray(sourceFile.parseDiagnostics) ? sourceFile.parseDiagnostics : [];
    const parserStatus = parseDiagnostics.length ? 'degraded' : 'observed';
    const importBindings = collectImportBindings(sourceFile);
    const symbolAt = createFileSymbolResolver(sourceFile);
    const collectImportCalls = createImportCallCollector(sourceFile, importBindings, symbolAt);
    const collectSameFileStoreActionCalls = createSameFileStoreActionCallCollector(sourceFile, importBindings, symbolAt);
    const collectSameFileDirectCalls = createSameFileDirectCallCollector(sourceFile, symbolAt);
    const collectStoreHookSelectors = createStoreHookSelectorCollector(sourceFile, importBindings, symbolAt);
    const collectImmutableKeyTruthinessGuard = createImmutableKeyTruthinessGuardCollector(sourceFile, targetFile);
    const collectFiniteNumberGuard = createFiniteNumberGuardCollector(sourceFile, targetFile, symbolAt);
    const collectZodBroaderCast = createZodObjectBroaderCastCollector(sourceFile, targetFile, importBindings, symbolAt);
    const collectUnawaitedAsyncHelper = createUnawaitedAsyncHelperCollector(sourceFile, targetFile, symbolAt);
    const collectCaughtAsyncAwait = createCaughtAsyncAwaitCollector(sourceFile, targetFile);
    const collectReturnedQueueCatch = createReturnedQueueCatchCollector(sourceFile, targetFile, symbolAt);
    const collectRepositoryBulkDirectWrite = createRepositoryBulkDirectWriteCollector(sourceFile, targetFile, importBindings, symbolAt);
    const collectFailedSafeParseRawFallback = createFailedSafeParseRawFallbackCollector(sourceFile, targetFile, symbolAt);
    const collectRepositoryDirectReadReturn = createRepositoryDirectReadReturnCollector(sourceFile, targetFile, importBindings, symbolAt);
    const symbols = [];
    const fileFeatures = new Set();
    const structuredOutputCandidates = [];
    let structuredOutputCandidatesOmitted = 0;
    if (parserStatus === 'observed') {
        fileFeatures.add('ParserEvidence:ImportedPropertyEventCallsV1');
    }
    let gemScoreTotal = 0;

    // 1. Semantic Scoring (Fast Scan)
    for (const [cat, tokens] of Object.entries(SEMANTIC_TOKENS)) {
        for (const token of tokens) {
            if (semanticTokenMatches(sourceCode, token)) {
                fileFeatures.add(`Tech:${token}`);
                fileFeatures.add(`Cat:${cat}`);
                gemScoreTotal += (GEM_WEIGHTS[cat] || 1.0);
            }
        }
    }
    for (const feature of collectStateFlowFeatures(sourceFile, importBindings)) {
        fileFeatures.add(feature);
    }
    for (const feature of collectConfiguredFrameworkFeatures(sourceFile, importBindings)) {
        fileFeatures.add(feature);
    }
    for (const feature of collectReactRuntimeFeatures(sourceFile, importBindings)) {
        fileFeatures.add(feature);
    }
    for (const feature of collectReactHookFlowFeatures(sourceFile, importBindings, symbolAt)) {
        fileFeatures.add(feature);
    }
    for (const feature of collectReactMutationContextFeatures(sourceFile)) {
        fileFeatures.add(feature);
    }
    for (const feature of collectReactResponseStateFeatures(sourceFile, sourceCode, importBindings, symbolAt)) {
        fileFeatures.add(feature);
    }
    for (const feature of collectFormValidationFeatures(sourceFile, importBindings)) {
        fileFeatures.add(feature);
    }
    for (const feature of collectApiBoundaryFeatures(sourceFile, importBindings, targetFile)) {
        fileFeatures.add(feature);
    }
    for (const feature of collectTestRuntimeFeatures(sourceFile, importBindings, targetFile)) {
        fileFeatures.add(feature);
    }
    for (const feature of collectStylingFeatures(sourceFile, importBindings, targetFile)) {
        fileFeatures.add(feature);
    }

    // 2. High-Fidelity AST Traversal
    function visit(node) {
        if (parserStatus === 'observed') {
            collectImmutableKeyTruthinessGuard(node, fileFeatures);
            collectFiniteNumberGuard(node, fileFeatures);
            collectZodBroaderCast(node, fileFeatures);
            collectUnawaitedAsyncHelper(node, fileFeatures);
            collectCaughtAsyncAwait(node, fileFeatures);
            collectReturnedQueueCatch(node, fileFeatures);
            collectRepositoryBulkDirectWrite(node, fileFeatures);
            collectFailedSafeParseRawFallback(node, fileFeatures);
            collectRepositoryDirectReadReturn(node, fileFeatures);
            const candidate = structuredXmlSinkCandidate(node, sourceFile, importBindings, symbolAt);
            if (candidate) {
                if (structuredOutputCandidates.length < 16) structuredOutputCandidates.push(candidate);
                else structuredOutputCandidatesOmitted += 1;
            }
        }
        if (ts.isVariableStatement(node)) {
            const isExported = node.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword);
            const isDefault = node.modifiers?.some(m => m.kind === ts.SyntaxKind.DefaultKeyword);
            node.declarationList.declarations.forEach(decl => {
                const varName = decl.name.getText(sourceFile);
                let type = 'Variable';
                let init = decl.initializer;
                const spanEndNode = decl.initializer || decl;
                while (init && (ts.isAsExpression(init) || ts.isParenthesizedExpression(init))) init = init.expression;
                if (init && (ts.isArrowFunction(init) || ts.isFunctionExpression(init))) {
                    type = varName.startsWith('use')
                        ? 'Hook'
                        : (/^[A-Z]/.test(varName) && hasReactComponentEvidence(init, decl, sourceFile))
                            ? 'Component'
                            : 'Arrow';
                }
                if (isExported || type === 'Hook' || type === 'Component') {
                    const emitterEvidence = type === 'Variable'
                        ? eventEmitterSingletonEvidence(decl, sourceFile, importBindings, symbolAt) : null;
                    const factoryEvidence = type === 'Variable' && isExported
                        ? factorySourceEvidence(decl, sourceFile, importBindings, symbolAt) : null;
                    addSymbolEntry(varName, type, decl, node, {
                        spanStartNode: decl,
                        spanEndNode,
                        exported: isExported,
                        exportKind: isDefault ? 'default' : 'named',
                        modifiers: getModifierNames(node),
                        initializerMemberEvidence: collectInitializerMemberEvidence(decl.initializer, sourceFile, importBindings, collectImportCalls, symbolAt),
                        ...(emitterEvidence ? {event_emitter_singleton_evidence: emitterEvidence} : {}),
                        ...(factoryEvidence ? {factory_source_evidence: factoryEvidence} : {}),
                    });
                }
            });
        } else if (ts.isClassDeclaration(node) && node.name) {
            const modifiers = getModifierNames(node);
            addSymbolEntry(node.name.getText(sourceFile), 'Class', node, node, {
                exported: modifiers.includes('export'),
                exportKind: modifiers.includes('default') ? 'default' : (modifiers.includes('export') ? 'named' : 'local'),
                modifiers,
                extends: getHeritageTypes(node, ts.SyntaxKind.ExtendsKeyword, sourceFile),
                implements: getHeritageTypes(node, ts.SyntaxKind.ImplementsKeyword, sourceFile),
                members: getMemberNames(node, sourceFile),
                memberDetails: getMemberDetails(node, sourceFile, importBindings, collectImportCalls, symbolAt),
                class_instance_event_call_evidence: classInstanceEventCallEvidence(
                    node, sourceFile, importBindings, symbolAt),
            });
        } else if (ts.isFunctionDeclaration(node) && node.name) {
            const modifiers = getModifierNames(node);
            addSymbolEntry(node.name.getText(sourceFile), 'Function', node, node, {
                exported: modifiers.includes('export'),
                exportKind: modifiers.includes('default') ? 'default' : (modifiers.includes('export') ? 'named' : 'local'),
                modifiers,
            });
        } else if (ts.isExportAssignment(node)) {
            const exportedExpression = node.expression?.getText(sourceFile) || '';
            const exportedName = /^[A-Za-z_$][\w$]*$/.test(exportedExpression) ? exportedExpression : '';
            addSymbolEntry('default_export', 'DefaultExport', node, node, {
                exported: true,
                exportKind: 'default',
                modifiers: [],
                exportedNames: exportedName ? [exportedName] : [],
                dependencies: exportedName ? [exportedName] : [],
            });
        } else if (ts.isInterfaceDeclaration(node)) {
            const isExported = node.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword);
            if (isExported) {
                const modifiers = getModifierNames(node);
                addSymbolEntry(node.name.getText(sourceFile), 'Interface', node, node, {
                    exported: true,
                    exportKind: modifiers.includes('default') ? 'default' : 'named',
                    modifiers,
                    extends: getHeritageTypes(node, ts.SyntaxKind.ExtendsKeyword, sourceFile),
                    members: getMemberNames(node, sourceFile),
                    memberDetails: getMemberDetails(node, sourceFile, importBindings, collectImportCalls, symbolAt),
                });
            }
        } else if (ts.isTypeAliasDeclaration(node)) {
            const isExported = node.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword);
            if (isExported) {
                const modifiers = getModifierNames(node);
                addSymbolEntry(node.name.getText(sourceFile), 'TypeDefinition', node, node, {
                    exported: true,
                    exportKind: modifiers.includes('default') ? 'default' : 'named',
                    modifiers,
                });
            }
        } else if (ts.isExportDeclaration(node)) {
            const moduleSpecifier = node.moduleSpecifier?.getText(sourceFile).replace(/['"`]/g, '');
            if (moduleSpecifier) {
                const exportedNames = [];
                if (node.exportClause && ts.isNamedExports(node.exportClause)) {
                    for (const el of node.exportClause.elements) {
                        const exportedName = el.name.getText(sourceFile);
                        const localName = el.propertyName ? el.propertyName.getText(sourceFile) : exportedName;
                        exportedNames.push(exportedName);
                        addSymbolEntry(exportedName, 'ReExportedSymbol', el, node, {
                            exported: true,
                            exportKind: node.isTypeOnly ? 'type' : 'reexport',
                            modifiers: [],
                            moduleSpecifier,
                            exportedNames: [exportedName],
                            dependencies: localName && localName !== exportedName ? [localName] : [],
                        });
                    }
                }
                addSymbolEntry(`proxy:${moduleSpecifier}`, 'ProxyExport', node, node, {
                    exported: true,
                    exportKind: 'reexport',
                    modifiers: [],
                    moduleSpecifier,
                    exportedNames,
                });
            } else if (node.exportClause && ts.isNamedExports(node.exportClause)) {
                // Handles local re-export surfaces: `export { foo, bar as baz }`
                for (const el of node.exportClause.elements) {
                    const exportedName = el.name.getText(sourceFile);
                    const localName = el.propertyName ? el.propertyName.getText(sourceFile) : exportedName;
                    addSymbolEntry(exportedName, 'LocalReExport', el, node, {
                        exported: true,
                        exportKind: node.isTypeOnly ? 'type' : 'named',
                        modifiers: [],
                        exportedNames: [exportedName],
                        dependencies: localName ? [localName] : [],
                    });
                }
            }
        }

        ts.forEachChild(node, visit);
    }

    function nodeEndWithoutTrailingTrivia(node) {
        if (node && typeof node.end === 'number') {
            return node.end;
        }
        return node.getEnd(sourceFile);
    }

    function lineNumberAtPosition(position) {
        return sourceFile.getLineAndCharacterOfPosition(Math.max(0, position)).line + 1;
    }

    function addSymbolEntry(name, type, node, container, extra = {}) {
        const { spanStartNode, spanEndNode, ...symbolExtra } = extra;
        const startNode = spanStartNode || container;
        const endNode = spanEndNode || container;
        const start = startNode.getStart(sourceFile);
        const end = nodeEndWithoutTrailingTrivia(endNode);
        const line = lineNumberAtPosition(start);
        const endLine = Math.max(line, lineNumberAtPosition(Math.max(start, end - 1)));
        const logicIdentity = buildLogicIdentity(name, type, node, sourceFile);
        const symbol = {
            name,
            type,
            start,
            end,
            line,
            endLine,
            sourceLines: `L${line}-L${endLine}`,
            signature: extractSignature(node, sourceFile),
            dna: generateDna(sourceCode.substring(start, end)),
            logicDna: logicIdentity.logicDna,
            semanticSignature: logicIdentity.semanticSignature,
            canonicalSymbolType: canonicalSymbolType(type, name, [], logicIdentity.frameworkTags),
            frameworkTags: logicIdentity.frameworkTags,
            normalizationProfile: logicIdentity.normalizationProfile,
            semanticDepth: logicIdentity.semanticDepth,
            logicDnaKind: logicIdentity.logicDnaKind,
            normalizationConfidence: logicIdentity.normalizationConfidence,
            parserKind: logicIdentity.parserKind,
            parserVersion: logicIdentity.parserVersion,
            dependencies: [],
            features: [],
            exported: false,
            exportKind: 'local',
            modifiers: [],
            extends: [],
            implements: [],
            members: [],
            memberDetails: [],
            moduleSpecifier: '',
            exportedNames: [],
            dependencyImports: [],
            dynamicImports: [],
            uiDependencies: [],
            architecturalMarkers: [],
            runtimeContract: false,
            contractKind: '',
            import_call_evidence: collectImportCalls(node),
            ...((type === 'Function' || type === 'Arrow' || type === 'Hook' || type === 'Component')
                ? {same_file_store_action_call_evidence: collectSameFileStoreActionCalls(node),
                    same_file_direct_call_evidence: collectSameFileDirectCalls(node),
                    store_hook_selector_evidence: collectStoreHookSelectors(node)} : {}),
            ...symbolExtra,
        };

        if (symbol.exported) {
            const contract = detectRuntimeExportContract(targetFile, fileFeatures, name);
            if (contract.matched) {
                symbol.features.push('Contract:RuntimeDiscovered');
                symbol.features.push(`ContractKind:${contract.kind}`);
                symbol.runtimeContract = true;
                symbol.contractKind = contract.kind;
                fileFeatures.add('Contract:RuntimeSurface');
            }
            let declarationText = '';
            try {
                declarationText = node?.getText ? node.getText(sourceFile) : '';
            } catch (_err) {
                declarationText = '';
            }
            const gqlContract = detectGraphQLExportContract(sourceFile, sourceCode, targetFile, name, declarationText);
            if (gqlContract.matched) {
                symbol.features.push('Contract:GraphQLDocument');
                symbol.features.push(`ContractKind:${gqlContract.kind}`);
                if (!symbol.contractKind) {
                    symbol.contractKind = gqlContract.kind;
                }
                fileFeatures.add('Contract:GraphQLSurface');
            }
        }

        // References collection
        ts.forEachChild(container, function walk(inner) {
            if (ts.isIdentifier(inner)) {
                const id = inner.getText(sourceFile);
                if (id.length > 2 && id !== name && !symbol.dependencies.includes(id)) {
                    symbol.dependencies.push(id);
                    const marker = ARCH_MARKERS.find(m => id.endsWith(m) || id.startsWith(m));
                    if (marker) {
                        symbol.features.push(`Arch:${marker}`);
                        if (!symbol.architecturalMarkers.includes(marker)) {
                            symbol.architecturalMarkers.push(marker);
                        }
                    }
                }
                const importBinding = importBindings.get(id);
                if (importBinding && !symbol.dependencyImports.some(entry => entry.localName === importBinding.localName && entry.source === importBinding.source)) {
                    symbol.dependencyImports.push(importBinding);
                }
            } else if (ts.isJsxOpeningElement(inner) || ts.isJsxSelfClosingElement(inner)) {
                const tagName = inner.tagName.getText(sourceFile);
                if (!symbol.dependencies.includes(tagName)) {
                    symbol.dependencies.push(`ui:${tagName}`);
                    symbol.features.push('Feature:UIComponent');
                }
                if (!symbol.uiDependencies.includes(tagName)) {
                    symbol.uiDependencies.push(tagName);
                }
            } else if (ts.isCallExpression(inner)) {
                const expr = inner.expression;
                if (expr.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(expr) && expr.text === 'require')) {
                    const arg = inner.arguments[0];
                    if (arg && ts.isStringLiteral(arg)) {
                        symbol.dependencies.push(`dynamic:${arg.text}`);
                        symbol.features.push('Feature:DynamicImport');
                        if (!symbol.dynamicImports.includes(arg.text)) {
                            symbol.dynamicImports.push(arg.text);
                        }
                    }
                }
            }
            ts.forEachChild(inner, walk);
        });

        symbol.features = [...new Set(symbol.features)];
        symbol.canonicalSymbolType = canonicalSymbolType(symbol.type, symbol.name, symbol.features, symbol.frameworkTags);
        symbols.push(symbol);
    }

    visit(sourceFile);

    // Global Sentinel
    const loc = sourceFile.getLineAndCharacterOfPosition(sourceFile.getEnd()).line + 1;
    symbols.push({
        name: '__file_meta__',
        type: 'Meta',
        start: 0,
        end: 0,
        signature: 'File Level Genomic Data',
        dna: '',
        dependencies: [],
        parserStatus,
        parserKind: 'typescript_compiler_api',
        semanticDepth: parserStatus === 'observed' ? 'ast_normalized' : 'partial_ast',
        parserDiagnosticCount: parseDiagnostics.length,
        parserDiagnosticCodes: [...new Set(parseDiagnostics.map(item => String(item.code)))].slice(0, 20),
        structuredOutputScanStatus: parserStatus === 'observed' ? 'observed' : 'unavailable_parser',
        structuredOutputScanVersion: 'v3-xml-mime-helper-argument-candidate',
        structuredOutputCandidates,
        structuredOutputCandidatesOmitted,
        moduleImports: collectModuleImportEvidence(sourceFile),
        directImportBindings: collectDirectImportBindingEvidence(sourceFile),
        moduleRootImportCallEvidence: collectImportCalls(sourceFile, 'module_root'),
        features: [
            ...fileFeatures,
            `ParserStatus:${parserStatus}`,
            `ParserDiagnosticCount:${parseDiagnostics.length}`,
            `GemScore:${gemScoreTotal.toFixed(1)}`,
            `Hash:${generateDna(sourceCode)}`,
            `MTime:${fs.statSync(targetFile).mtimeMs / 1000}`,
            `LOC:${loc}`
        ]
    });

    return symbols;
}

const args = process.argv.slice(2);
const targetFiles = [];
let batchJson = false;
let batchMetrics = false;
let workerJsonl = false;
let requestId = '';
let workerMaxRequests = 200;
let workerMaxRssBytes = 805306368;

for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--batch-json") {
        batchJson = true;
        continue;
    }
    if (arg === "--batch-metrics") {
        batchMetrics = true;
        continue;
    }
    if (arg === "--worker-jsonl") {
        workerJsonl = true;
        batchMetrics = true;
        continue;
    }
    if (arg === "--request-id" && args[i + 1]) {
        requestId = args[i + 1];
        i += 1;
        continue;
    }
    if (arg === "--max-requests" && args[i + 1]) {
        workerMaxRequests = Math.max(1, Number.parseInt(args[i + 1], 10) || workerMaxRequests);
        i += 1;
        continue;
    }
    if (arg === "--max-rss-bytes" && args[i + 1]) {
        workerMaxRssBytes = Math.max(1, Number.parseInt(args[i + 1], 10) || workerMaxRssBytes);
        i += 1;
        continue;
    }
    if (arg === "--doctrine-json" && args[i + 1]) {
        const doctrinePath = args[i + 1];
        if (fs.existsSync(doctrinePath)) {
            try {
                const doctrine = JSON.parse(fs.readFileSync(doctrinePath, 'utf8'));
                if (doctrine.semantic_taxonomy) SEMANTIC_TOKENS = doctrine.semantic_taxonomy;
                if (doctrine.semantic_weights) GEM_WEIGHTS = doctrine.semantic_weights;
                if (doctrine.architectural_markers) ARCH_MARKERS = doctrine.architectural_markers;
                if (doctrine.side_effect_governance) {
                    if (doctrine.side_effect_governance.identifiers) SIDE_EFFECT_IDENTIFIERS = doctrine.side_effect_governance.identifiers;
                    if (doctrine.side_effect_governance.property_patterns) SIDE_EFFECT_PROPERTY_PATTERNS = doctrine.side_effect_governance.property_patterns;
                }
            } catch (e) {
                // Silently fall back to hardcoded defaults if doctrine is corrupt
            }
        }
        i += 1;
        continue;
    }
    if (arg === "--path-entry" && args[i + 1]) {
        targetFiles.push(Buffer.from(args[i + 1], 'base64').toString('utf8'));
        i += 1;
        continue;
    }
    targetFiles.push(arg);
}

function analyzeBatch(batchFiles, batchRequestId) {
    const results = {};
    const startedAt = process.hrtime.bigint();
    const rssStartBytes = process.memoryUsage().rss;
    let rssMaxObservedBytes = rssStartBytes;
    for (const targetFile of batchFiles) {
        results[targetFile] = analyzeFile(targetFile);
        rssMaxObservedBytes = Math.max(rssMaxObservedBytes, process.memoryUsage().rss);
    }
    const elapsedMilliseconds = Number(process.hrtime.bigint() - startedAt) / 1e6;
    return {
        batchMeta: {
            protocolVersion: 1,
            requestId: batchRequestId,
            filesRequested: batchFiles.length,
            filesReported: Object.keys(results).length,
            rssStartBytes,
            rssMaxObservedBytes,
            elapsedMilliseconds
        },
        results
    };
}

if (workerJsonl) {
    const readline = require('readline');
    let requestsHandled = 0;
    const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
    input.on('line', (line) => {
        if (!line.trim()) return;
        let incomingRequestId = '';
        try {
            const request = JSON.parse(line);
            incomingRequestId = String(request.requestId || '');
            if (request.command === 'shutdown') {
                input.close();
                return;
            }
            if (request.protocolVersion !== 1 || !incomingRequestId || !Array.isArray(request.paths)) {
                throw new Error('invalid_worker_request');
            }
            const envelope = analyzeBatch(request.paths.map(String), incomingRequestId);
            requestsHandled += 1;
            const rssBytes = Number(envelope.batchMeta.rssMaxObservedBytes || 0);
            if (requestsHandled >= workerMaxRequests) {
                envelope.batchMeta.workerRetireReason = 'request_limit';
            } else if (rssBytes >= workerMaxRssBytes) {
                envelope.batchMeta.workerRetireReason = 'rss_limit';
            }
            process.stdout.write(JSON.stringify(envelope) + '\n');
            if (envelope.batchMeta.workerRetireReason) {
                input.close();
            }
        } catch (err) {
            process.stdout.write(JSON.stringify({
                batchMeta: {
                    protocolVersion: 1,
                    requestId: incomingRequestId,
                    filesRequested: 0,
                    filesReported: 0
                },
                results: {},
                error: String(err && err.message ? err.message : err)
            }) + '\n');
        }
    });
} else if (!targetFiles.length) {
    process.exit(1);
} else try {
    if (batchJson || targetFiles.length > 1) {
        const envelope = analyzeBatch(targetFiles, requestId);
        if (batchMetrics) {
            process.stdout.write(JSON.stringify(envelope));
        } else {
            process.stdout.write(JSON.stringify(envelope.results));
        }
    } else {
        const results = analyzeFile(targetFiles[0]);
        process.stdout.write(JSON.stringify(results));
    }
} catch (err) { 
    process.exit(1); 
}
