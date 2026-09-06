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

function getMemberDetails(node, sourceFile, importBindings) {
    if (!node.members) return [];
    const details = [];
    for (const member of node.members) {
        if (!member.name || typeof member.name.getText !== 'function') continue;
        const name = member.name.getText(sourceFile);
        if (!name) continue;
        const usage = collectNodeDependencies(member, sourceFile, importBindings, name);
        details.push({
            name,
            kind: getMemberKind(member),
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
        });
    }
    return details;
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

function collectReactHookFlowFeatures(sourceFile, importBindings) {
    const fileFeatures = new Set();
    const importedFrom = new Map();

    for (const [localName, binding] of importBindings.entries()) {
        importedFrom.set(localName, binding.source);
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
            const callName = getExpressionName(node.expression, sourceFile);
            const source = importedFrom.get(callName) || '';
            
            if (['useEffect', 'useLayoutEffect', 'useMemo', 'useCallback'].includes(callName)) {
                if (node.arguments.length < 2) {
                    fileFeatures.add(`Hook:MissingDeps:${callName}`);
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
                        fileFeatures.add(`Hook:DynamicDeps:${callName}`);
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
    const symbols = [];
    const fileFeatures = new Set();
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
    for (const feature of collectReactHookFlowFeatures(sourceFile, importBindings)) {
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
                    type = varName.startsWith('use') ? 'Hook' : (/^[A-Z]/.test(varName) ? 'Component' : 'Arrow');
                }
                if (isExported || type === 'Hook' || type === 'Component') {
                    addSymbolEntry(varName, type, decl, node, {
                        spanStartNode: decl,
                        spanEndNode,
                        exported: isExported,
                        exportKind: isDefault ? 'default' : 'named',
                        modifiers: getModifierNames(node),
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
                memberDetails: getMemberDetails(node, sourceFile, importBindings),
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
                    memberDetails: getMemberDetails(node, sourceFile, importBindings),
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

for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--batch-json") {
        batchJson = true;
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

if (!targetFiles.length) {
    process.exit(1);
}

try {
    if (batchJson || targetFiles.length > 1) {
        const results = {};
        for (const targetFile of targetFiles) {
            results[targetFile] = analyzeFile(targetFile);
        }
        process.stdout.write(JSON.stringify(results));
    } else {
        const results = analyzeFile(targetFiles[0]);
        process.stdout.write(JSON.stringify(results));
    }
} catch (err) { 
    process.exit(1); 
}
