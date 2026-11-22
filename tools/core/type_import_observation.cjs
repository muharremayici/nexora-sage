'use strict';
// One private same-Program cached lookup; no resolver call, host or path reader.
const crypto = require('node:crypto');
const { hashObject } = require('./ts_input_image_experiment.cjs');
const { JoinedRefusal } = require('./owned_image_handoff.cjs');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const fail = code => { throw new JoinedRefusal(code); };

function singleTypeImport(ts, source, specifier, additionalNamespace = null) {
    if (!source || !Array.isArray(source.statements) || !Array.isArray(source.parseDiagnostics))
        fail('type_import_source_scope_refused');
    let imports = source.statements.filter(node => ts.isImportDeclaration(node));
    if(additionalNamespace!==null){
        reactNamespaceImport(ts,source,additionalNamespace);
        imports=imports.slice(1);
    }
    if (source.parseDiagnostics.length || imports.length !== 1) fail('type_import_source_scope_refused');
    const node = imports[0], clause = node.importClause, names = clause?.namedBindings;
    if (!clause?.isTypeOnly || clause.name || !names || !ts.isNamedImports(names) || names.elements.length !== 1
        || names.elements[0].propertyName || names.elements[0].isTypeOnly
        || !ts.isIdentifier(names.elements[0].name) || !ts.isStringLiteral(node.moduleSpecifier)
        || node.moduleSpecifier.text !== specifier || node.attributes) fail('type_import_source_scope_refused');
    return { node, name: names.elements[0].name.text };
}

function reactNamespaceImport(ts,source,name){
    const imports=source.statements.filter(node=>ts.isImportDeclaration(node)),react=imports[0],clause=react?.importClause;
    if(source.parseDiagnostics.length||imports.length!==2||source.statements[0]!==react||!clause||clause.isTypeOnly||clause.name
        ||!clause.namedBindings||!ts.isNamespaceImport(clause.namedBindings)||clause.namedBindings.name.text!==name
        ||!ts.isStringLiteral(react.moduleSpecifier)||react.moduleSpecifier.text!=='react'||react.attributes)fail('react_namespace_import_refused');
    return imports;
}

function observeReactProgram(ts,program,context) {
    const {sourcePath,dtoPath,sourceHostSha256,dtoHostSha256,packageRows,pins}=context;
    const byPath=new Map(packageRows.map(row=>[row.path.replace(/\\/g,'/'),row]));
    const source=program.getSourceFile(sourcePath),dto=program.getSourceFile(dtoPath);
    if(!source||sha(source.text)!==sourceHostSha256||!dto||sha(dto.text)!==dtoHostSha256)fail('react_program_text_refused');
    const imported=singleTypeImport(ts,source,'./types','React');
    const edges=[];
    function edge(file,node,sourceRole,sourceHash,expectedPackage=null,localSource=null){
        const exported=!!context.barrelSource&&ts.isExportDeclaration(node);
        if(exported&&(node.isTypeOnly||node.exportClause?.elements?.[0]?.isTypeOnly))fail('barrel_value_export_refused');
        const extension=localSource?(localSource.role==='source:barrel'?ts.Extension.Ts:ts.Extension.Tsx):ts.Extension.Dts;
        const resolved=program.getResolvedModuleFromModuleSpecifier(node.moduleSpecifier,file)?.resolvedModule;
        if(!resolved||resolved.extension!==extension)fail('react_cached_edge_refused');
        const normalized=resolved.resolvedFileName.replace(/\\/g,'/');
        const row=byPath.get(normalized);
        if(expectedPackage){
            if(!row||row.package!==expectedPackage||row.member!=='index.d.ts'||resolved.isExternalLibraryImport!==true
                ||resolved.packageId?.name!==expectedPackage||resolved.packageId?.version!==pins[expectedPackage])fail('react_cached_package_refused');
        }else if(normalized!==(localSource?localSource.path:dtoPath).replace(/\\/g,'/')||resolved.isExternalLibraryImport!==false||resolved.packageId)fail('react_cached_local_refused');
        const target=program.getSourceFile(resolved.resolvedFileName);
        const targetHash=expectedPackage?row.host_sha256:localSource?localSource.host_sha256:dtoHostSha256;
        if(!target||target.isDeclarationFile!==(localSource?false:true)||sha(target.text)!==targetHash
            ||context.barrelSource&&localSource&&target.scriptKind!==(extension===ts.Extension.Ts?ts.ScriptKind.TS:ts.ScriptKind.TSX))fail('react_cached_target_refused');
        edges.push({source_role:sourceRole,target_role:expectedPackage?row.role:localSource?localSource.role:'declaration',
            source_path_sha256:sha(file.fileName),target_path_sha256:sha(target.fileName),
            source_host_sha256:sourceHash,target_host_sha256:targetHash,
            module_specifier_sha256:sha(node.moduleSpecifier.text),type_only:exported?node.isTypeOnly:node.importClause.isTypeOnly,
            ...(context.barrelSource?{syntax_kind:exported?'export':'import'}:{}),
            extension,external:resolved.isExternalLibraryImport,
            package_id:expectedPackage?{present:true,name:resolved.packageId.name,version:resolved.packageId.version,sub_module:resolved.packageId.subModuleName}
                :{present:false,name:'',version:'',sub_module:''}});
        return {file:target,row};
    }
    let entryFile,barrelFile;
    if(context.entrySource){
        entryFile=program.getSourceFile(context.entrySource.path);
        if(!entryFile||sha(entryFile.text)!==context.entrySource.host_sha256)fail('two_source_program_text_refused');
        const entryImports=reactNamespaceImport(ts,entryFile,'React');
        edge(entryFile,entryImports[0],context.entrySource.role,context.entrySource.host_sha256,'@types/react');
        edge(entryFile,entryImports[1],context.entrySource.role,context.entrySource.host_sha256,null,context.barrelSource||context.componentSource);
        if(context.barrelSource){
            barrelFile=program.getSourceFile(context.barrelSource.path);
            if(!barrelFile||sha(barrelFile.text)!==context.barrelSource.host_sha256)fail('barrel_program_text_refused');
            edge(barrelFile,barrelFile.statements[0],context.barrelSource.role,context.barrelSource.host_sha256,null,context.componentSource);
        }
    }
    const sourceRole=context.componentSource?.role||'source';
    edge(source,imported.node,sourceRole,sourceHostSha256);
    const reactImport=source.statements[0],react=edge(source,reactImport,sourceRole,sourceHostSha256,'@types/react');
    const cssImports=react.file.statements.filter(node=>ts.isImportDeclaration(node)&&ts.isStringLiteral(node.moduleSpecifier)&&node.moduleSpecifier.text==='csstype');
    if(cssImports.length!==1||!cssImports[0].importClause)fail('react_css_import_refused');
    edge(react.file,cssImports[0],react.row.role,react.row.host_sha256,'csstype');
    const globalRow=packageRows.find(row=>row.package==='@types/react'&&row.member==='global.d.ts');
    const globalFile=globalRow&&program.getSourceFile(globalRow.path);
    if(!globalFile||sha(globalFile.text)!==globalRow.host_sha256
        ||!react.file.referencedFiles.some(ref=>ref.fileName==='global.d.ts'))fail('react_global_reference_refused');
    const fn=source.statements.find(node=>ts.isFunctionDeclaration(node));
    const tag=fn?.body?.statements[0]?.expression?.tagName;
    const checker=program.getTypeChecker(),actual=tag&&checker.getSymbolAtLocation(tag);
    const alias=checker.getSymbolAtLocation(reactImport.importClause.namedBindings.name);
    const module=alias&&(alias.flags&ts.SymbolFlags.Alias?checker.getAliasedSymbol(alias):alias);
    const jsx=module&&checker.getExportsOfModule(module).find(symbol=>symbol.name==='JSX');
    const intrinsic=jsx&&checker.getExportsOfModule(jsx).find(symbol=>symbol.name==='IntrinsicElements');
    const tagName=context.componentContract?.tag || 'span';
    const expectedTag=intrinsic&&checker.getDeclaredTypeOfSymbol(intrinsic).getProperty(tagName);
    if(!actual||!expectedTag||actual!==expectedTag||tag.text!==tagName||!expectedTag.declarations?.length
        ||expectedTag.declarations.some(node=>node.getSourceFile()!==react.file||node.parent.name?.text!=='IntrinsicElements'))fail('react_actual_jsx_origin_refused');
    let componentFact;
    if(entryFile){
        const tag=entryFile.statements[2].declarationList.declarations[0].initializer.tagName;
        const importedName=entryFile.statements[1].importClause.namedBindings.elements[0].name;
        const alias=checker.getSymbolAtLocation(importedName),actualTag=checker.getSymbolAtLocation(tag);
        const target=alias&&(alias.flags&ts.SymbolFlags.Alias)?checker.getAliasedSymbol(alias):null;
        if(!alias||actualTag!==alias||target!==checker.getSymbolAtLocation(fn.name)
            ||!target.declarations?.length||target.declarations.some(node=>node!==fn))fail('two_source_component_symbol_refused');
        const propertyName=fn.body.statements[0].expression.attributes.properties[0].initializer.expression.name.text;
        const props=checker.getTypeAtLocation(fn.parameters[0].name),property=checker.getPropertyOfType(props,propertyName);
        const type=property&&checker.getTypeOfSymbolAtLocation(property,fn.parameters[0]);
        if(!property?.declarations?.length||property.declarations.some(node=>node.getSourceFile()!==dto)
            ||![ts.TypeFlags.String,ts.TypeFlags.Number].includes(type?.flags))fail('two_source_props_origin_refused');
        componentFact={source_role:context.entrySource.role,target_role:context.componentSource.role,
            component_host_sha256:sourceHostSha256,actual_tag_alias_matched:true,same_program_export_symbol:true,
            props_declaration_role:'declaration',property_type_kind:type.flags===ts.TypeFlags.String?'string':'number',
            ...(barrelFile?observeBarrelAlias(ts,checker,entryFile,barrelFile,fn,context.barrelSource):{})};
    }
    const dom=context.domLibrary ? observeDomProgram(ts,program,checker,fn,react,globalFile,globalRow,context) : null;
    return {edges,...(componentFact?{imported_component:componentFact}:{}),...(dom?{dom_observation:dom}:{}),jsx_origin:{namespace:'React.JSX',intrinsic:tagName,package:'@types/react',
        version:pins['@types/react'],target_role:react.row.role,host_text_sha256:react.row.host_sha256,
        source_path_sha256:sha(react.file.fileName),same_program_symbol_match:true,
        global_reference_role:globalRow.role,global_reference_host_sha256:globalRow.host_sha256}};
}

function observeBarrelAlias(ts,checker,entry,barrel,fn,owner){
    if(typeof checker.getImmediateAliasedSymbol!=='function'||typeof checker.getExportsOfModule!=='function')fail('barrel_alias_api_unavailable');
    const imported=entry.statements[1].importClause.namedBindings.elements[0];
    const exported=barrel.statements[0].exportClause.elements[0];
    const entryAlias=checker.getSymbolAtLocation(imported.name),module=checker.getSymbolAtLocation(barrel);
    const symbols=module&&checker.getExportsOfModule(module),barrelAlias=symbols?.[0];
    const target=checker.getSymbolAtLocation(fn.name);
    const owns=(symbol,node)=>!!symbol?.declarations?.length&&symbol.declarations.every(declaration=>declaration===node);
    if(!owns(module,barrel)||!Array.isArray(symbols)||symbols.length!==1||!owns(entryAlias,imported)||!owns(barrelAlias,exported)
        ||!owns(target,fn)||!(entryAlias.flags&ts.SymbolFlags.Alias)||!(barrelAlias.flags&ts.SymbolFlags.Alias)
        ||barrelAlias.name!==exported.name.text||checker.getSymbolAtLocation(exported.name)!==barrelAlias)
        fail('barrel_export_symbol_refused');
    if(checker.getImmediateAliasedSymbol(entryAlias)!==barrelAlias||checker.getImmediateAliasedSymbol(barrelAlias)!==target
        ||checker.getAliasedSymbol(entryAlias)!==target||checker.getAliasedSymbol(barrelAlias)!==target)
        fail('barrel_alias_chain_refused');
    return {barrel_role:owner.role,barrel_host_sha256:owner.host_sha256,barrel_export_declaration_matched:true,
        entry_immediate_barrel_alias:true,barrel_immediate_function:true,flattened_targets_matched:true};
}

function observeDomProgram(ts,program,checker,fn,react,globalFile,globalRow,context){
    const rules=context.componentContract,library=context.domLibrary;
    const dom=program.getSourceFile(library.path);
    if(!rules||!dom||!dom.isDeclarationFile||sha(dom.text)!==library.host_sha256)fail('dom_program_text_refused');
    const jsx=fn.body.statements[0].expression;
    const attribute=jsx.attributes.properties.find(node=>node.name?.text===rules.event_attribute);
    const arrow=attribute?.initializer?.expression,param=arrow?.parameters?.[0];
    const value=arrow?.body?.statements?.[0]?.declarationList?.declarations?.[0]?.initializer;
    if(!arrow||!ts.isArrowFunction(arrow)||!param||!value||!ts.isPropertyAccessExpression(value)
        ||value.name.text!==rules.value_member||!ts.isPropertyAccessExpression(value.expression)
        ||value.expression.name.text!==rules.target_member)fail('dom_observation_shape_refused');
    const owned=(symbol,file)=>!!symbol?.declarations?.length&&symbol.declarations.every(node=>node.getSourceFile()===file);
    // A JSX attribute name owns a source symbol. Its contextual property owns
    // the React declaration; do not confuse the two declaration namespaces.
    const attributesType=checker.getContextualType(jsx.attributes);
    const attributeSymbol=attributesType&&checker.getPropertyOfType(attributesType,rules.event_attribute);
    if(!owned(attributeSymbol,react.file))fail('dom_event_attribute_origin_refused');
    const contextual=checker.getContextualType(arrow);
    const signatures=contextual?checker.getSignaturesOfType(checker.getNonNullableType(contextual),ts.SignatureKind.Call):[];
    if(signatures.length!==1||signatures[0].parameters.length!==1)fail('dom_contextual_signature_refused');
    const expected=checker.getTypeOfSymbolAtLocation(signatures[0].parameters[0],arrow);
    const actual=checker.getTypeAtLocation(param.name);
    if(expected!==actual||actual.flags&(ts.TypeFlags.Any|ts.TypeFlags.Unknown)
        ||actual.symbol?.name!==rules.event_interface||!owned(actual.symbol,react.file))fail('dom_contextual_parameter_refused');
    const args=checker.getTypeArguments(actual);
    if(args.length!==1||args[0].symbol?.name!==rules.dom_interface)fail('dom_input_interface_refused');
    const owners=new Set();
    for(const declaration of args[0].symbol.declarations||[]){
        const file=declaration.getSourceFile();
        if(file===dom)owners.add('compiler:lib.dom.d.ts');
        else if(file===globalFile)owners.add(globalRow.role);
        else fail('dom_input_interface_origin_refused');
    }
    if(!owners.has('compiler:lib.dom.d.ts')||owners.size>2)fail('dom_input_interface_origin_refused');
    const target=checker.getSymbolAtLocation(value.expression.name);
    if(!owned(target,react.file)||checker.getPropertyOfType(actual,rules.target_member)!==target)fail('dom_current_target_origin_refused');
    const valueSymbol=checker.getSymbolAtLocation(value.name),targetType=checker.getTypeAtLocation(value.expression);
    const valueType=checker.getTypeAtLocation(value);
    if(!owned(valueSymbol,dom)||checker.getPropertyOfType(targetType,rules.value_member)!==valueSymbol
        ||valueSymbol.declarations.some(node=>node.parent.name?.text!==rules.dom_interface)
        ||valueType.flags!==ts.TypeFlags.String)fail('dom_value_origin_refused');
    const locations=context.locationPolicy?observeDomLocations(ts,checker,fn.getSourceFile(),{
        event_attribute:attribute.name,parameter_declaration:param.name,parameter_reference:value.expression.expression,
        current_target_member:value.expression.name,value_member:value.name,value_access:value},context.locationPolicy,context.locationBinding):null;
    return {...(locations?{source_locations:locations}:{}),attribute_role:react.row.role,event_type_role:react.row.role,event_type_host_sha256:react.row.host_sha256,
        current_target_role:react.row.role,value_role:'compiler:lib.dom.d.ts',dom_raw_sha256:library.raw_sha256,
        dom_host_sha256:library.host_sha256,dom_bytes:library.bytes,value_declaring_interface:rules.dom_interface,
        event_type_name:rules.event_interface,value_type_kind:'string',contextual_parameter_matched:true,
        same_program_value_symbol:true,input_interface_owner_roles:[...owners].sort()};
}

function observeDomLocations(ts,checker,source,nodes,policy,binding){
    const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
    const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
    if(!policy||policy.record_count!==Object.keys(nodes).length||!exact(nodes,policy.roles)
        ||!exact(binding,['parent_handoff_sha256','source_raw_sha256','source_host_sha256','leading_bom_utf16_units_removed'])
        ||!hash(binding.parent_handoff_sha256)||!hash(binding.source_raw_sha256)||!hash(binding.source_host_sha256)
        ||![0,1].includes(binding.leading_bom_utf16_units_removed)||typeof source?.text!=='string'
        ||sha(source.text)!==binding.source_host_sha256)fail('dom_location_context_refused');
    const parameter=nodes.parameter_declaration,reference=nodes.parameter_reference;
    const symbol=checker.getSymbolAtLocation(parameter);
    if(!ts.isIdentifier(parameter)||!ts.isIdentifier(reference)||!symbol||symbol!==checker.getSymbolAtLocation(reference)
        ||!symbol.declarations?.includes(parameter.parent)||!ts.isParameter(parameter.parent))fail('dom_location_parameter_symbol_refused');
    const arrow=parameter.parent.parent,accessNode=nodes.value_access;
    if(!ts.isArrowFunction(arrow)||arrow.parameters[0]!==parameter.parent
        ||nodes.event_attribute.parent.initializer?.expression!==arrow
        ||arrow.body.statements?.[0]?.declarationList?.declarations?.[0]?.initializer!==accessNode
        ||!ts.isPropertyAccessExpression(accessNode)||accessNode.name!==nodes.value_member
        ||accessNode.expression.name!==nodes.current_target_member||accessNode.expression.expression!==reference)
        fail('dom_location_shape_refused');
    const spans={};
    for(const [role,node] of Object.entries(nodes)){
        if(node.getSourceFile()!==source)fail('dom_location_source_refused');
        const start=node.getStart(source),end=node.getEnd();
        const splits=n=>n<source.text.length&&source.text.charCodeAt(n)>=0xdc00&&source.text.charCodeAt(n)<=0xdfff;
        if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||end<=start||end>source.text.length
            ||splits(start)||splits(end))fail('dom_location_span_refused');
        if(role!=='value_access'&&(!ts.isIdentifier(node)||source.text.slice(start,end)!==node.text))fail('dom_location_token_refused');
        spans[role]={start,end,line:source.getLineAndCharacterOfPosition(start).line+1,
            end_line:source.getLineAndCharacterOfPosition(end-1).line+1};
    }
    const access=spans.value_access;
    for(const role of ['parameter_reference','current_target_member','value_member'])
        if(spans[role].start<access.start||spans[role].end>access.end)fail('dom_location_containment_refused');
    return {version:policy.version,authority:policy.authority,source_role:policy.source_role,offset_unit:policy.offset_unit,
        ...binding,same_program_parameter_symbol:true,spans};
}

function createTypeImportObserver(ts, binding, joined, sourceHostSha256, typingPackages = null, imagePolicy = null, sourceTransform = null, projectSources = null) {
    const expected = Object.freeze(JSON.parse(JSON.stringify(binding)));
    const sourcePath = joined.compiler_virtual_path, targetPath = joined.auxiliary_virtual_path;
    const specifier = joined.module_specifier, version = joined.type_import_observation_version;
    const allowedCalls = typingPackages ? joined.max_programs : joined.max_import_edges;
    if (allowedCalls !== 1 || typeof sourceHostSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(sourceHostSha256))
        fail('type_import_observer_context_refused');
    const identity = hashObject(expected);
    const componentHash=projectSources?projectSources.component.host_sha256:sourceHostSha256;
    if(joined.source_location_contract&&(!sourceTransform||sourceTransform.raw_sha256!==(projectSources?expected.sources.component.raw_sha256:expected.source_sha256)
        ||sourceTransform.host_sha256!==componentHash))fail('dom_location_context_refused');
    let calls = 0, observed = null, disposed = false;
    const api = Object.create(ts);
    Object.defineProperty(api, 'createProgram', { value: options => {
        if (disposed || ++calls !== allowedCalls) fail('type_import_invocation_refused');
        const program = ts.createProgram(options);
        if (!program || typeof program.getResolvedModuleFromModuleSpecifier !== 'function'
            || typeof program.getSourceFile !== 'function') fail('type_import_cached_api_unavailable');
        const source = program.getSourceFile(sourcePath);
        if (!source || source.fileName !== sourcePath || sha(source.text) !== sourceHostSha256)
            fail('type_import_program_source_refused');
        if(typingPackages){
            const value=observeReactProgram(ts,program,{sourcePath:projectSources?projectSources.component.path:sourcePath,dtoPath:targetPath,sourceHostSha256:componentHash,
                entrySource:projectSources?.entry,componentSource:projectSources?.component,barrelSource:projectSources?.barrel,
                dtoHostSha256:expected.auxiliary_host_sha256,pins:typingPackages.policy.pins,componentContract:joined.component_contract,
                domLibrary:imagePolicy?.dom_library?{...imagePolicy.dom_library,path:imagePolicy.dom_library.virtual_path}:null,
                locationPolicy:joined.source_location_contract||null,
                locationBinding:sourceTransform?{parent_handoff_sha256:identity,source_raw_sha256:sourceTransform.raw_sha256,
                    source_host_sha256:componentHash,leading_bom_utf16_units_removed:sourceTransform.leading_bom_utf16_units_removed}:null,
                packageRows:typingPackages.entries.map(row=>({...row,role:'package:'+row.package+'/'+row.member}))});
            if(value.edges.length!==joined.max_import_edges)fail('react_cached_edge_budget_refused');
            observed=Object.freeze({version:joined.type_import_observation_version,authority:'producer_observed_only',
                status:'matched_cached_package_context',handoff_sha256:identity,compiler_version:ts.version,...value,
                program_completeness_established:false,native_project_equivalent:false,semantic_lineage:false,production_enabled:false});
            return program;
        }
        const imported = singleTypeImport(ts, source, specifier);
        if (sha(imported.name) !== expected.import_name_sha256) fail('type_import_name_refused');
        const resolution = program.getResolvedModuleFromModuleSpecifier(imported.node.moduleSpecifier, source)?.resolvedModule;
        if (!resolution || resolution.resolvedFileName !== targetPath || resolution.extension !== ts.Extension.Dts
            || resolution.isExternalLibraryImport !== false || resolution.packageId)
            fail('type_import_cached_resolution_refused');
        const target = program.getSourceFile(targetPath);
        if (!target || target.fileName !== targetPath || !target.isDeclarationFile
            || sha(target.text) !== expected.auxiliary_host_sha256) fail('type_import_program_target_refused');
        observed = Object.freeze({ version, authority: 'producer_observed_only', status: 'matched_cached_resolution',
            handoff_sha256: identity, compiler_version: ts.version, source_path_sha256: sha(sourcePath),
            source_host_sha256: sourceHostSha256, target_path_sha256: sha(targetPath),
            target_host_sha256: expected.auxiliary_host_sha256, module_specifier_sha256: sha(specifier),
            import_name_sha256: expected.import_name_sha256, type_only: true, extension: '.d.ts',
            is_external_library_import: false, compiler_invocations: 1, program_completeness_established: false,
            native_project_equivalent: false, semantic_lineage: false, production_enabled: false });
        return program;
    } });
    return Object.freeze({ api,
        project(result) {
            if (disposed || calls !== 1 || !observed || result?.status !== 'DIAGNOSTIC_EXPERIMENT_NOT_ADMITTED'
                || result.handoff_sha256 !== identity || result.production_enabled !== false || result.semantic_lineage !== false
                || result.native_project_equivalent !== false || !Array.isArray(result.source_files))
                fail('type_import_result_refused');
            const pairs=typingPackages?observed.edges.flatMap(row=>[[row.source_path_sha256,row.source_host_sha256],[row.target_path_sha256,row.target_host_sha256]])
                :[[observed.source_path_sha256, observed.source_host_sha256],[observed.target_path_sha256, observed.target_host_sha256]];
            for (const [path, text] of pairs) {
                const rows = result.source_files.filter(row => row.path_sha256 === path);
                if (rows.length !== 1 || rows[0].text_sha256 !== text) fail('type_import_result_refused');
            }
            if(observed.dom_observation){
                const dom=imagePolicy.dom_library,path=sha(dom.virtual_path);
                const rows=result.source_files.filter(row=>row.path_sha256===path);
                if(rows.length!==1||rows[0].text_sha256!==dom.host_sha256
                    ||!result.host_calls?.some(row=>row.operation==='readFile'&&row.path_sha256===path))
                    fail('dom_result_input_refused');
            }
            return observed;
        },
        dispose() { disposed = true; observed = null; },
    });
}
module.exports = { singleTypeImport, createTypeImportObserver, observeReactProgram, observeDomProgram, observeDomLocations, observeBarrelAlias, reactNamespaceImport };
