'use strict';
// Explicit private type-only grammar. Existing no-import admissions stay separate.
const crypto = require('node:crypto');
const { hashObject, admitTypingPackages } = require('./ts_input_image_experiment.cjs');
const { checkSourceBinding, decodeOwnedSourceBytes, canonical, JoinedRefusal } = require('./owned_image_handoff.cjs');
const { decodeOwnedDeclarationBytes, createDeclarationExperiment: createCapturedExperiment, sourceRecords } = require('./owned_declaration_handoff.cjs');
const { decodeCapturedConfig } = require('./owned_config_handoff.cjs');
const { singleTypeImport, createTypeImportObserver, reactNamespaceImport } = require('./type_import_observation.cjs');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const fail = code => { throw new JoinedRefusal(code); };
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const EXTRA_KEYS = ['auxiliary_atlas_path', 'auxiliary_virtual_path', 'auxiliary_raw_sha256', 'auxiliary_host_sha256',
    'auxiliary_context_sha256', 'auxiliary_bytes', 'config_atlas_path', 'config_virtual_path', 'config_raw_sha256',
    'config_host_sha256', 'config_context_sha256', 'config_bytes', 'capture_policy_sha256', 'import_name_sha256'];

function admitDeclarationHandoff(ts, request, expected, policies, compilerReference) {
    const { joined, bridge, image, capture } = policies;
    const barrel=joined.version==='v11-private-react-barrel';
    const two=barrel||joined.version==='v10-private-react-two-source';
    const locations=two||joined.version==='v9-private-react-dom-source-locations';
    const dom=locations||joined.version==='v8-private-react-dom-context';
    const react = dom || joined.version === 'v7-private-react-package-context';
    const tsx = react || joined.version === 'v6-private-synthetic-tsx';
    const objectMode = tsx || joined.version === 'v5-private-single-object-type';
    if ((!objectMode && joined.version !== 'v4-private-single-type-import')
        || objectMode && (joined.max_type_properties !== 1 || joined.declaration_shape !== 'single_required_primitive_property')
        || joined.max_import_edges !== (barrel?6:two?5:react?3:1) || react&&joined.max_programs!==1 || joined.module_specifier !== './types'
        || joined.max_source_files !== (barrel?3:two?2:1) || joined.max_auxiliary_files !== (tsx && !react ? 3 : 2) || joined.default_enabled !== false
        || joined.production_enabled !== false || joined.semantic_lineage !== false || bridge.default_enabled !== false
        || bridge.production_enabled !== false || bridge.semantic_lineage !== false || image.default_enabled !== false
        || image.production_enabled !== false || image.lineage_enabled !== false || !capture
        || capture.version !== (barrel?'v8-private-react-barrel-capture':two?'v7-private-react-two-source-capture':dom?'v6-private-react-dom-captured-config':react ? 'v5-private-react-captured-config' : tsx ? 'v4-private-tsx-captured-config' : 'v3-private-captured-config')
        || joined.capture_profile !== (barrel?'private_owned_react_barrel_capture':two?'private_owned_react_two_source_capture':dom?'private_owned_react_dom_capture':react ? 'private_owned_react_capture' : tsx ? 'private_owned_tsx_config_handoff' : 'private_owned_config_handoff'))
        fail('type_import_policy_refused');
    for (const key of ['max_source_files', 'max_auxiliary_files', 'max_auxiliary_bytes', 'max_config_bytes', 'max_request_bytes',
        'bridge_profile', 'image_profile', 'parser_virtual_path', 'compiler_virtual_path', 'atlas_relative_path',
        'auxiliary_virtual_path', 'auxiliary_atlas_path', 'config_virtual_path', 'config_atlas_path',
        'input_observation_version', 'input_observation_schema', 'input_observation_authority'])
        if (joined[key] !== capture[key]) fail('type_import_capture_policy_refused');
    if (tsx && !react && (joined.source_script_kind !== 'TSX' || bridge.script_kind !== 'TSX'
        || ['jsx_atlas_path','jsx_virtual_path','max_jsx_bytes'].some(key=>joined[key]!==capture[key])
        || joined.max_jsx_bytes!==8192)) fail('tsx_policy_refused');
    if(react&&(joined.source_script_kind!=='TSX'||bridge.script_kind!=='TSX'||joined.react_namespace!=='React'
        ||!policies.packages||image.package_policy_sha256!==hashObject(policies.packages)))fail('react_context_policy_refused');
    if(dom&&!image.dom_library)fail('dom_context_policy_refused');
    if(locations!==!!joined.source_location_contract)fail('dom_location_policy_refused');
    if (!exact(request, ['version', 'binding', two?'sources':'source', 'auxiliary', 'config', ...(react ? ['packages'] : tsx ? ['jsx'] : [])]) || request.version !== joined.version)
        fail('type_import_request_refused');
    const binding = request.binding;
    checkSourceBinding(ts, binding, expected, policies, compilerReference,
        objectMode ? [...EXTRA_KEYS, 'property_name_sha256', ...(two?['sources']:[]), ...(react ? ['source_script_kind','package_policy_sha256','package_context_sha256'] : tsx ? ['source_script_kind','jsx_atlas_path','jsx_virtual_path',
            'jsx_raw_sha256','jsx_host_sha256','jsx_context_sha256','jsx_bytes','jsx_policy_sha256'] : [])] : EXTRA_KEYS);
    if(tsx&&!react&&(binding.source_script_kind!=='TSX'||binding.jsx_policy_sha256!==hashObject(joined.jsx_contract)
        ||binding.jsx_context_sha256!==binding.config_context_sha256))fail('tsx_binding_refused');
    if (binding.capture_policy_sha256 !== hashObject(capture)) fail('type_import_capture_policy_refused');
    const sourceInputs=two?decodeProjectSources(request.sources,binding,policies):null;
    const source = two?sourceInputs.component:decodeOwnedSourceBytes(request.source, binding, policies);
    const parsed = ts.createSourceFile(two?joined.source_roster.component.compiler_path:joined.compiler_virtual_path, source.text, ts.ScriptTarget.Latest, true);
    const imported = singleTypeImport(ts, parsed, joined.module_specifier,react?joined.react_namespace:null);
    if(tsx)admitTsxComponent(ts,parsed,imported,binding,react?joined.component_contract:joined.jsx_contract,react?1:0,dom,two);
    if(two){
        if(parsed.statements[2].name.text!==joined.component_export_name)fail('two_source_export_refused');
        const entry=ts.createSourceFile(joined.compiler_virtual_path,sourceInputs.entry.text,ts.ScriptTarget.Latest,true);
        const imports=reactNamespaceImport(ts,entry,joined.react_namespace),node=imports[1],clause=node.importClause;
        if(entry.scriptKind!==ts.ScriptKind.TSX||entry.statements.length!==3||entry.statements[1]!==node
            ||!clause||clause.isTypeOnly||clause.name||!clause.namedBindings||!ts.isNamedImports(clause.namedBindings)
            ||clause.namedBindings.elements.length!==1||clause.namedBindings.elements[0].propertyName
            ||clause.namedBindings.elements[0].isTypeOnly||clause.namedBindings.elements[0].name.text!==joined.component_export_name
            ||!ts.isStringLiteral(node.moduleSpecifier)||node.moduleSpecifier.text!==joined.component_import_specifier||node.attributes)
            fail('two_source_entry_refused');
        admitTsxUse(ts,entry.statements[2],joined.component_export_name,binding);
        const pre=ts.preProcessFile(sourceInputs.entry.text,true,true);
        if(pre.referencedFiles.length||pre.typeReferenceDirectives.length)fail('two_source_entry_refused');
        if(barrel)admitBarrel(ts,sourceInputs.barrel.text,joined);
    }
    const pre = ts.preProcessFile(source.text, true, true);
    if (parsed.isDeclarationFile || pre.importedFiles.length !== (react?2:1) || pre.referencedFiles.length || pre.typeReferenceDirectives.length
        || /(^|[\r\n])\s*\/\/\/\s*<reference\b/.test(source.text) || sha(imported.name) !== binding.import_name_sha256)
        fail('type_import_source_scope_refused');
    const pending = [parsed];
    while (pending.length) {
        const node = pending.pop();
        if (ts.isImportEqualsDeclaration(node) || ts.isExportDeclaration(node) || ts.isImportTypeNode(node)
            || ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword
                || ts.isIdentifier(node.expression) && node.expression.text === 'require')) fail('type_import_source_scope_refused');
        ts.forEachChild(node, child => { pending.push(child); });
    }
    const auxiliaryRaw = decodeOwnedDeclarationBytes(request.auxiliary, binding, policies);
    const declarationText = auxiliaryRaw.toString('utf8').replace(/^\ufeff/, '');
    const declaration = ts.createSourceFile(joined.auxiliary_virtual_path, declarationText, ts.ScriptTarget.Latest, true);
    const alias = declaration.statements[0], auxPre = ts.preProcessFile(declarationText, true, true);
    if (!declaration.isDeclarationFile || declaration.parseDiagnostics.length || declaration.statements.length !== 1
        || !ts.isTypeAliasDeclaration(alias) || alias.name.text !== imported.name || alias.typeParameters
        || alias.modifiers?.length !== 1 || alias.modifiers[0].kind !== ts.SyntaxKind.ExportKeyword
        || auxPre.importedFiles.length || auxPre.referencedFiles.length || auxPre.typeReferenceDirectives.length
        || /(^|[\r\n])\s*\/\/\/\s*<reference\b/.test(declarationText)) fail('type_import_declaration_scope_refused');
    if (objectMode) {
        if (!ts.isTypeLiteralNode(alias.type) || alias.type.members.length !== joined.max_type_properties)
            fail('object_type_shape_refused');
        const property = alias.type.members[0];
        if (!ts.isPropertySignature(property) || !ts.isIdentifier(property.name) || property.questionToken
            || property.modifiers?.length || property.initializer || !property.type
            || ![ts.SyntaxKind.NumberKeyword, ts.SyntaxKind.StringKeyword].includes(property.type.kind))
            fail('object_type_shape_refused');
        if (sha(property.name.text) !== binding.property_name_sha256) fail('object_type_property_refused');
    } else if (![ts.SyntaxKind.NumberKeyword, ts.SyntaxKind.StringKeyword].includes(alias.type.kind)) {
        fail('type_import_declaration_scope_refused');
    }
    const configRaw = decodeCapturedConfig(ts, request.config, binding, policies);
    let jsxRaw,typingPackages;
    if(react){
        if(binding.source_script_kind!=='TSX'||binding.package_policy_sha256!==hashObject(policies.packages)
            ||binding.package_context_sha256!==policies.packages.inventory_sha256)fail('react_package_binding_refused');
        typingPackages=admitTypingPackages(request.packages,policies.packages);
    }else if(tsx){
        jsxRaw=decodeOwnedDeclarationBytes(request.jsx,{
            auxiliary_atlas_path:binding.jsx_atlas_path,auxiliary_virtual_path:binding.jsx_virtual_path,
            auxiliary_bytes:binding.jsx_bytes,auxiliary_raw_sha256:binding.jsx_raw_sha256,auxiliary_host_sha256:binding.jsx_host_sha256},
            {...policies,joined:{...joined,auxiliary_atlas_path:joined.jsx_atlas_path,
                auxiliary_virtual_path:joined.jsx_virtual_path,max_auxiliary_bytes:joined.max_jsx_bytes}});
        admitJsxDeclaration(ts,jsxRaw,joined.jsx_contract);
    }
    const frozen = Object.freeze(JSON.parse(canonical(binding)));
    return Object.freeze({ ...(two?sourceInputs.entry:source), ...(two?{sourceInputs}:{}), auxiliaryRaw, configRaw, ...(react?{typingPackages}:tsx ? {jsxRaw} : {}), binding: frozen, bindingSha256: hashObject(frozen) });
}

function decodeProjectSources(wire,binding,policies){
    const {joined,bridge}=policies,roster=joined.source_roster;
    const barrel=joined.version==='v11-private-react-barrel';
    if(!exact(roster,barrel?['entry','barrel','component']:['entry','component'])||!exact(wire,Object.keys(roster))||!exact(binding.sources,Object.keys(roster)))fail('two_source_roster_refused');
    const inputs={},paths=new Set(),requests=new Set();let bytes=0;
    for(const [key,owner] of Object.entries(roster)){
        const row=binding.sources[key];
        const selected=barrel?policies.bridges?.[owner.bridge_profile]:bridge;
        if(barrel&&(!selected||!row||selected.default_enabled!==false||selected.production_enabled!==false||selected.semantic_lineage!==false
            ||selected.max_files!==1||owner.script_kind!==(key==='barrel'?'TS':'TSX')
            ||owner.parser_protocol!==(key==='barrel'?2:3)||selected.script_kind!==(key==='barrel'?undefined:'TSX')
            ||row.bridge_profile!==owner.bridge_profile||row.bridge_version!==selected.version
            ||row.bridge_policy_sha256!==hashObject(selected)||row.parser_protocol!==owner.parser_protocol))fail('barrel_bridge_binding_refused');
        if(!exact(row,['atlas_path','compiler_path','parser_virtual_path','parser_request_id','parser_response_sha256','raw_sha256','host_sha256','bytes',
            ...(barrel?['bridge_profile','bridge_version','bridge_policy_sha256','parser_protocol']:[])])
            ||row.atlas_path!==owner.atlas_path||row.compiler_path!==owner.compiler_path||row.parser_virtual_path!==selected.virtual_file
            ||typeof row.parser_request_id!=='string'||!/^[A-Za-z0-9_-]+$/.test(row.parser_request_id)||row.parser_request_id.length>selected.max_request_id_chars
            ||requests.has(row.parser_request_id)||paths.has(row.compiler_path)
            ||!['parser_response_sha256','raw_sha256','host_sha256'].every(k=>typeof row[k]==='string'&&/^[a-f0-9]{64}$/.test(row[k])))fail('two_source_binding_refused');
        const decoded=decodeOwnedSourceBytes(wire[key],{source_bytes:row.bytes,source_sha256:row.raw_sha256},{...policies,bridge:selected});
        if(sha(decoded.text.replace(/^\ufeff/,''))!==row.host_sha256)fail('two_source_host_refused');
        inputs[key]=decoded;bytes+=decoded.raw.length;paths.add(row.compiler_path);requests.add(row.parser_request_id);
    }
    const entry=binding.sources.entry;
    if(bytes>joined.max_total_source_bytes||entry.raw_sha256!==binding.source_sha256||entry.bytes!==binding.source_bytes
        ||entry.parser_response_sha256!==binding.parser_response_sha256||entry.compiler_path!==binding.compiler_virtual_path
        ||entry.atlas_path!==binding.atlas_relative_path)fail('two_source_entry_binding_refused');
    return Object.freeze(inputs);
}

function admitBarrel(ts,text,joined){
    const file=ts.createSourceFile(joined.source_roster.barrel.compiler_path,text,ts.ScriptTarget.Latest,true);
    const node=file.statements[0],names=node?.exportClause,pre=ts.preProcessFile(text,true,true);
    if(file.scriptKind!==ts.ScriptKind.TS||file.isDeclarationFile||file.parseDiagnostics.length||file.statements.length!==1
        ||!ts.isExportDeclaration(node)||node.isTypeOnly||node.modifiers?.length||node.attributes
        ||!node.moduleSpecifier||!ts.isStringLiteral(node.moduleSpecifier)||node.moduleSpecifier.text!==joined.barrel_module_specifier
        ||!names||!ts.isNamedExports(names)||names.elements.length!==1||names.elements[0].isTypeOnly
        ||names.elements[0].propertyName||!ts.isIdentifier(names.elements[0].name)||names.elements[0].name.text!==joined.component_export_name
        ||pre.importedFiles.length!==1||pre.referencedFiles.length||pre.typeReferenceDirectives.length
        ||/(^|[\r\n])\s*\/\/\/\s*<reference\b/.test(text))fail('barrel_source_shape_refused');
}

function admitJsxDeclaration(ts, raw, rules) {
    if (!exact(rules,['version','namespace','tag','attribute','attribute_type','element_members',
        'max_intrinsic_elements','max_attributes','max_bytes'])
        || rules.version !== 'v1-synthetic-jsx-span-title' || rules.namespace !== 'JSX' || rules.tag !== 'span'
        || rules.attribute !== 'title' || rules.attribute_type !== 'string' || rules.element_members !== 0
        || rules.max_intrinsic_elements !== 1 || rules.max_attributes !== 1 || rules.max_bytes !== 8192)
        fail('tsx_jsx_policy_refused');
    const file=ts.createSourceFile('jsx.d.ts',raw.toString('utf8').replace(/^\ufeff/,''),ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);
    const ns=file.statements[0];
    if (!file.isDeclarationFile || file.parseDiagnostics.length || file.statements.length!==1
        || ts.isExternalModule(file) || !ts.isModuleDeclaration(ns) || !ts.isIdentifier(ns.name) || ns.name.text!==rules.namespace
        || ns.modifiers?.length!==1 || ns.modifiers[0].kind!==ts.SyntaxKind.DeclareKeyword || !ts.isModuleBlock(ns.body)
        || ns.body.statements.length!==2) fail('tsx_jsx_contract_refused');
    const [element,intrinsic]=ns.body.statements;
    if (![element,intrinsic].every(row=>ts.isInterfaceDeclaration(row) && !row.typeParameters && !row.heritageClauses && !row.modifiers?.length)
        || element.name.text!=='Element' || element.members.length!==0
        || intrinsic.name.text!=='IntrinsicElements' || intrinsic.members.length!==1) fail('tsx_jsx_contract_refused');
    const tag=intrinsic.members[0];
    const plain=(row,name)=>ts.isPropertySignature(row)&&ts.isIdentifier(row.name)&&row.name.text===name
        &&!row.questionToken&&!row.modifiers?.length&&!row.initializer;
    if(!plain(tag,rules.tag)||!ts.isTypeLiteralNode(tag.type)||tag.type.members.length!==1
        ||!plain(tag.type.members[0],rules.attribute)||tag.type.members[0].type?.kind!==ts.SyntaxKind.StringKeyword)
        fail('tsx_jsx_contract_refused');
}

function admitTsxComponent(ts, source, imported, binding, rules, extraImports=0, dom=false, componentOnly=false) {
    if(source.scriptKind!==ts.ScriptKind.TSX || source.statements.length!==3+extraImports-(componentOnly?1:0)) fail('tsx_source_shape_refused');
    const fn=source.statements[1+extraImports], value=source.statements[2+extraImports];
    if(!ts.isFunctionDeclaration(fn)||!fn.name||!ts.isIdentifier(fn.name)||fn.typeParameters||fn.asteriskToken||fn.type
        ||fn.modifiers?.length!==1||fn.modifiers[0].kind!==ts.SyntaxKind.ExportKeyword||fn.parameters.length!==1
        ||!fn.body||!ts.isBlock(fn.body)||fn.body.statements.length!==1
        ||!ts.isReturnStatement(fn.body.statements[0]))fail('tsx_source_shape_refused');
    const param=fn.parameters[0], expression=fn.body.statements[0].expression;
    if(!ts.isIdentifier(param.name)||param.questionToken||param.dotDotDotToken||param.initializer||param.modifiers?.length
        ||!param.type||!ts.isTypeReferenceNode(param.type)||!ts.isIdentifier(param.type.typeName)
        ||param.type.typeName.text!==imported.name||param.type.typeArguments
        ||!expression||!ts.isJsxSelfClosingElement(expression)||!ts.isIdentifier(expression.tagName)
        ||expression.tagName.text!==rules.tag||expression.typeArguments||expression.attributes.properties.length!==(dom?2:1))
        fail('tsx_source_shape_refused');
    const attribute=expression.attributes.properties[0], access=attribute.initializer?.expression;
    if(!ts.isJsxAttribute(attribute)||!ts.isIdentifier(attribute.name)||attribute.name.text!==rules.attribute
        ||!attribute.initializer||!ts.isJsxExpression(attribute.initializer)||!access||!ts.isPropertyAccessExpression(access)
        ||access.questionDotToken||!ts.isIdentifier(access.expression)||access.expression.text!==param.name.text
        ||sha(access.name.text)!==binding.property_name_sha256)fail('tsx_source_shape_refused');
    if(dom)admitDomCallback(ts,expression.attributes.properties[1],rules);
    if(!componentOnly)admitTsxUse(ts,value,fn.name.text,binding);
}

function admitTsxUse(ts,value,componentName,binding){
    if(!ts.isVariableStatement(value)||value.modifiers?.length!==1||value.modifiers[0].kind!==ts.SyntaxKind.ExportKeyword
        ||value.declarationList.flags!==ts.NodeFlags.Const||value.declarationList.declarations.length!==1)
        fail('tsx_source_shape_refused');
    const decl=value.declarationList.declarations[0], call=decl.initializer;
    if(!ts.isIdentifier(decl.name)||decl.type||!call||!ts.isJsxSelfClosingElement(call)
        ||!ts.isIdentifier(call.tagName)||call.tagName.text!==componentName||call.typeArguments
        ||call.attributes.properties.length!==1)fail('tsx_source_shape_refused');
    const prop=call.attributes.properties[0], initializer=prop.initializer;
    if(!ts.isJsxAttribute(prop)||!ts.isIdentifier(prop.name)||sha(prop.name.text)!==binding.property_name_sha256
        ||!initializer||!(ts.isStringLiteral(initializer)||ts.isJsxExpression(initializer)
            &&initializer.expression&&ts.isNumericLiteral(initializer.expression)))fail('tsx_source_shape_refused');
}

function admitDomCallback(ts,attribute,rules){
    const arrow=attribute.initializer?.expression;
    if(!ts.isJsxAttribute(attribute)||!ts.isIdentifier(attribute.name)||attribute.name.text!==rules.event_attribute
        ||!attribute.initializer||!ts.isJsxExpression(attribute.initializer)||!arrow||!ts.isArrowFunction(arrow)
        ||arrow.modifiers?.length||arrow.typeParameters||arrow.type||arrow.parameters.length!==1
        ||!ts.isBlock(arrow.body)||arrow.body.statements.length!==1)fail('dom_callback_shape_refused');
    const parameter=arrow.parameters[0],statement=arrow.body.statements[0];
    if(!ts.isIdentifier(parameter.name)||parameter.type||parameter.questionToken||parameter.dotDotDotToken||parameter.initializer||parameter.modifiers?.length
        ||!ts.isVariableStatement(statement)||statement.modifiers?.length||statement.declarationList.flags!==ts.NodeFlags.Const
        ||statement.declarationList.declarations.length!==1)fail('dom_callback_shape_refused');
    const decl=statement.declarationList.declarations[0],value=decl.initializer,target=value?.expression;
    if(!ts.isIdentifier(decl.name)||!decl.type||![ts.SyntaxKind.StringKeyword,ts.SyntaxKind.NumberKeyword].includes(decl.type.kind)
        ||!value||!ts.isPropertyAccessExpression(value)||value.questionDotToken||value.name.text!==rules.value_member
        ||!target||!ts.isPropertyAccessExpression(target)||target.questionDotToken||target.name.text!==rules.target_member
        ||!ts.isIdentifier(target.expression)||target.expression.text!==parameter.name.text)fail('dom_callback_shape_refused');
}

function createDeclarationExperiment(ts, admitted, policies, compilerEntries, metadataInput = null) {
    const projectSources=sourceRecords(admitted,policies.joined);
    const react=['v7-private-react-package-context','v8-private-react-dom-context','v9-private-react-dom-source-locations','v10-private-react-two-source','v11-private-react-barrel'].includes(policies.joined.version);
    const tsx=react||policies.joined.version==='v6-private-synthetic-tsx';
    let sourceKind;
    const kindApi=Object.create(ts);
    Object.defineProperty(kindApi,'createProgram',{value:options=>{
        const program=ts.createProgram(options);
        if(tsx){
            const source=program.getSourceFile(policies.joined.compiler_virtual_path);
            if(source?.scriptKind!==ts.ScriptKind.TSX)fail('tsx_program_kind_refused');
            sourceKind='TSX';
            if(projectSources){
                sourceKind={};
                for(const [key,row] of Object.entries(projectSources)){
                    const kind=policies.joined.source_roster[key].script_kind||'TSX';
                    if(program.getSourceFile(row.path)?.scriptKind!==ts.ScriptKind[kind])fail('tsx_program_kind_refused');
                    sourceKind[row.role]=kind;
                }
            }
        }
        return program;
    }});
    const locationSource=projectSources?admitted.sourceInputs.component:admitted;
    const observer = createTypeImportObserver(tsx?kindApi:ts, admitted.binding, policies.joined, sha(admitted.text.replace(/^\ufeff/, '')),admitted.typingPackages,policies.image,
        policies.joined.source_location_contract?{raw_sha256:sha(locationSource.raw),host_sha256:sha(locationSource.text.replace(/^\ufeff/,'')),
            leading_bom_utf16_units_removed:locationSource.text.startsWith('\ufeff')?1:0}:null,projectSources);
    const instance = createCapturedExperiment(observer.api, admitted, policies, compilerEntries, metadataInput);
    const cap = Math.min(policies.bridge.max_response_bytes, policies.image.limits.metadata_bytes);
    const scope = react?'finite_react_package_context_only':tsx ? 'synthetic_tsx_component_local_jsx_contract_only' : policies.joined.version === 'v5-private-single-object-type'
        ? 'synthetic_single_cached_object_type_import_only' : 'synthetic_single_cached_type_import_only';
    let disposed = false;
    return Object.freeze({
        inputIdentity(expected) {
            if (disposed) fail('declaration_disposed');
            return instance.inputIdentity(expected);
        },
        replay(expected) {
            if (disposed) fail('declaration_disposed');
            const result = instance.replay(expected);
            const projected = observer.project(result);
            const output = { ...result, type_import_observation: projected, scope, ...(tsx ? {source_script_kind:sourceKind} : {}) };
            if (Buffer.byteLength(JSON.stringify(output), 'utf8') > cap) fail('type_import_result_over_budget');
            return output;
        },
        dispose() { disposed = true; instance.dispose(); observer.dispose(); },
    });
}
module.exports = { admitDeclarationHandoff, createDeclarationExperiment };
