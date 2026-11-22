'use strict';
// Explicit private captured-config lane; no path reader or default activation.
const crypto = require('node:crypto');
const { hashObject } = require('./ts_input_image_experiment.cjs');
const { checkSourceBinding, decodeOwnedSource, canonical, JoinedRefusal } = require('./owned_image_handoff.cjs');
const { decodeOwnedDeclaration, createDeclarationExperiment } = require('./owned_declaration_handoff.cjs');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const fail = code => { throw new JoinedRefusal(code); };
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const AUX_KEYS = ['auxiliary_atlas_path', 'auxiliary_virtual_path', 'auxiliary_raw_sha256',
    'auxiliary_host_sha256', 'auxiliary_context_sha256', 'auxiliary_bytes'];
const CONFIG_KEYS = ['config_atlas_path', 'config_virtual_path', 'config_raw_sha256', 'config_host_sha256',
    'config_context_sha256', 'config_bytes'];

function admitDeclarationHandoff(ts, request, expected, policies, compilerReference) {
    const { joined, bridge, image } = policies;
    if (joined.version !== 'v3-private-captured-config' || joined.max_source_files !== 1 || joined.max_auxiliary_files !== 2
        || joined.default_enabled !== false || joined.production_enabled !== false || joined.semantic_lineage !== false
        || bridge.default_enabled !== false || bridge.production_enabled !== false || bridge.semantic_lineage !== false
        || image.default_enabled !== false || image.production_enabled !== false || image.lineage_enabled !== false
        || joined.parser_virtual_path !== bridge.virtual_file
        || joined.compiler_virtual_path !== '/__sage_ts_image__/project/entry.ts' || joined.atlas_relative_path !== 'entry.ts'
        || joined.auxiliary_virtual_path !== '/__sage_ts_image__/project/types.d.ts' || joined.auxiliary_atlas_path !== 'types.d.ts'
        || joined.config_virtual_path !== '/__sage_ts_image__/project/tsconfig.json' || joined.config_atlas_path !== 'tsconfig.json'
        || !Number.isSafeInteger(joined.max_config_bytes) || joined.max_config_bytes <= 0
        || !Number.isSafeInteger(joined.max_auxiliary_bytes) || joined.max_auxiliary_bytes <= 0)
        fail('config_policy_refused');
    if (!exact(request, ['version', 'binding', 'source', 'auxiliary', 'config']) || request.version !== joined.version)
        fail('config_request_refused');
    const binding = request.binding;
    checkSourceBinding(ts, binding, expected, policies, compilerReference, [...AUX_KEYS, ...CONFIG_KEYS]);
    const source = decodeOwnedSource(ts, request.source, binding, policies);
    const auxiliaryRaw = decodeOwnedDeclaration(ts, request.auxiliary, binding, policies);
    const raw = decodeCapturedConfig(ts, request.config, binding, policies);
    const frozen = Object.freeze(JSON.parse(canonical(binding)));
    return Object.freeze({ ...source, auxiliaryRaw, configRaw: raw, binding: frozen, bindingSha256: hashObject(frozen) });
}

function decodeCapturedConfig(ts, config, binding, policies) {
    const { joined, image } = policies;
    if (!exact(config, ['bytes', 'raw_sha256', 'host_text_sha256', 'utf8Base64'])
        || binding.config_atlas_path !== joined.config_atlas_path || binding.config_virtual_path !== joined.config_virtual_path
        || binding.config_context_sha256 !== binding.auxiliary_context_sha256
        || !Number.isSafeInteger(config.bytes) || config.bytes < 0
        || config.bytes > Math.min(joined.max_config_bytes, image.limits.auxiliary_file_bytes)
        || binding.config_bytes !== config.bytes || binding.config_raw_sha256 !== config.raw_sha256
        || binding.config_host_sha256 !== config.host_text_sha256 || typeof config.utf8Base64 !== 'string'
        || config.utf8Base64.length > Math.ceil(joined.max_config_bytes / 3) * 4)
        fail('config_input_refused');
    const raw = Buffer.from(config.utf8Base64, 'base64');
    if (raw.length !== config.bytes || sha(raw) !== config.raw_sha256 || raw.toString('base64') !== config.utf8Base64)
        fail('config_identity_refused');
    let text, parsed;
    try {
        text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(raw).replace(/^\ufeff/, '');
        parsed = JSON.parse(text);
    } catch { fail('config_syntax_refused'); }
    if (sha(text) !== config.host_text_sha256) fail('config_host_text_refused');
    if (!exact(parsed, ['files', 'compilerOptions'])
        || canonical(parsed.files) !== canonical([joined.atlas_relative_path, joined.auxiliary_atlas_path,
            ...(joined.input_observation_version === 'v4-private-tsx-input-summary' ? [joined.jsx_atlas_path] : [])])
        || canonical(parsed.compilerOptions) !== canonical(image.compiler_options)) fail('config_shape_refused');
    // Reuse the loaded TS JSON syntax tree to detect duplicate (including escaped) keys.
    // JSON.parse above rejects JSONC/trailing commas; no custom JSON parser or live read.
    const tree = ts.parseJsonText(joined.config_virtual_path, text);
    inspectConfigTree(ts,tree);
    return raw;
}
function inspectConfigTree(ts,tree,maxNodes=Infinity){
    const pending=[tree];
    if (tree.parseDiagnostics.length) fail('config_syntax_refused');
    let visited=0;
    while (pending.length) {
        if(++visited>maxNodes)fail('config_ast_budget_refused');
        const node = pending.pop();
        if (ts.isObjectLiteralExpression(node)) {
            const keys = new Set();
            for (const property of node.properties) {
                if (!ts.isPropertyAssignment(property) || typeof property.name?.text !== 'string'
                    || keys.has(property.name.text)) fail('config_duplicate_key_refused');
                keys.add(property.name.text);
            }
        }
        ts.forEachChild(node, child => { pending.push(child); });
    }
    return tree;
}

function admitConfigExtends(ts,request,expected,profiles,compilerReference){
    const policy=profiles.private_owned_config_extends,config=profiles[policy.config_limit_profile];
    const image=profiles[config.image_profile],cap=config.max_config_bytes;
    if(policy.version!=='v1-private-config-extends'||policy.compiler_version!==ts.version
        ||['default_enabled','production_enabled','semantic_lineage','cache_eligible'].some(k=>policy[k]!==false)
        ||!exact(policy.roster,['root','base'])||policy.selected_option!=='strict'
        ||!Number.isSafeInteger(policy.max_ast_nodes)||policy.max_ast_nodes<1||policy.max_ast_nodes>2*cap)
        fail('config_extends_policy_refused');
    if(!exact(request,['version','binding','files'])||request.version!==policy.version
        ||!exact(request.files,['root','base'])||hashObject(request.binding)!==hashObject(expected))fail('config_extends_binding_refused');
    const binding=request.binding;
    if(!exact(binding,['atlas_snapshot_id','atlas_sha256','invocation_id','compiler_version','compiler_source_sha256',
        'policy_sha256','input_policy_sha256','auxiliary_policy_sha256','context_sha256','files'])
        ||['atlas_snapshot_id','atlas_sha256','compiler_source_sha256','policy_sha256','input_policy_sha256','auxiliary_policy_sha256','context_sha256'].some(k=>!isDigest(binding[k]))
        ||typeof binding.invocation_id!=='string'||!/^[A-Za-z0-9_-]+$/.test(binding.invocation_id)
        ||binding.invocation_id.length>profiles[config.bridge_profile].max_request_id_chars
        ||binding.compiler_version!==ts.version||binding.compiler_source_sha256!==compilerReference
        ||binding.policy_sha256!==hashObject(policy)||binding.auxiliary_policy_sha256!==hashObject(profiles.atlas_auxiliary_inputs)
        ||binding.input_policy_sha256!==configExtendsPolicyIdentity(profiles,policy)
        ||!exact(binding.files,['root','base']))fail('config_extends_context_refused');
    const files=new Map();
    for(const role of ['root','base']){
        const row=binding.files[role],wire=request.files[role],owner=policy.roster[role];
        if(!exact(row,['atlas_path','virtual_path','bytes','raw_sha256','host_sha256'])
            ||row.atlas_path!==owner.atlas_path||row.virtual_path!==owner.virtual_path
            ||!exact(wire,['bytes','raw_sha256','host_text_sha256','utf8Base64'])
            ||!Number.isSafeInteger(wire.bytes)||wire.bytes<0||wire.bytes>cap||row.bytes!==wire.bytes
            ||!isDigest(row.raw_sha256)||!isDigest(row.host_sha256)||wire.raw_sha256!==row.raw_sha256||wire.host_text_sha256!==row.host_sha256
            ||typeof wire.utf8Base64!=='string'||wire.utf8Base64.length>Math.ceil(cap/3)*4)fail('config_extends_input_refused');
        const raw=Buffer.from(wire.utf8Base64,'base64');let text;
        if(raw.length!==row.bytes||sha(raw)!==row.raw_sha256||raw.toString('base64')!==wire.utf8Base64)fail('config_extends_input_refused');
        try{text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(raw).replace(/^\ufeff/,'');}
        catch{fail('config_extends_encoding_refused');}
        if(sha(text)!==row.host_sha256)fail('config_extends_input_refused');
        files.set(owner.virtual_path,{role,text,raw,identity:row});
    }
    return {policy,image,files,binding:JSON.parse(canonical(binding)),identity:hashObject(binding),policyIdentity:hashObject(policy)};
}

function configExtendsPolicyIdentity(profiles,policy){
    const config=profiles[policy.config_limit_profile];
    return hashObject({config,image:profiles[config.image_profile],bridge:profiles[config.bridge_profile],auxiliary:profiles.atlas_auxiliary_inputs});
}

const isDigest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const extendsFlags={production_enabled:false,semantic_lineage:false,cache_eligible:false,
    native_project_equivalent:false,program_completeness_established:false,loaded_code_attested:false,raw_config_retained:false};
const emptyConfigEdge=()=>({source_role:null,target_role:null,source_raw_sha256:null,source_host_sha256:null,
    target_raw_sha256:null,target_host_sha256:null,source_path_sha256:null,target_path_sha256:null,specifier_sha256:null,
    literal_host_span_utf16:{start:null,end:null},line:null,character:null});
const emptyStrictObservation=()=>({base:null,root_override:null,effective:null});

function createConfigExtendsHost(admitted){
    const {files,policy,image}=admitted,reads={root:0,base:0};
    let calls=0,denied=0,firstRefusal='';
    function refuse(code){denied++;firstRefusal=firstRefusal||code;fail(code);}
    function lookup(name){
        if(++calls>image.limits.host_calls)refuse('config_extends_host_budget_refused');
        const row=files.get(name);if(!row)refuse('config_extends_unowned_host_access');
        if(sha(row.raw)!==row.identity.raw_sha256||sha(row.text)!==row.identity.host_sha256)refuse('config_extends_held_input_changed');return row;
    }
    const host={useCaseSensitiveFileNames:true,getCurrentDirectory:()=>policy.project_virtual_root,
        readFile:name=>{const row=lookup(name);if(++reads[row.role]!==1)refuse('config_extends_repeat_read_refused');return row.text;},
        fileExists:name=>{lookup(name);return true;},
        readDirectory:()=>refuse('config_extends_directory_query_refused'),
        directoryExists:()=>refuse('config_extends_directory_query_refused'),
        realpath:()=>refuse('config_extends_realpath_refused')};
    return {host,summary:()=>({reads:{...reads},host_calls:calls,denied_host_calls:denied,host_refusal:firstRefusal})};
}

function configObject(ts,tree,limit){
    inspectConfigTree(ts,tree,limit);
    const errors=[],value=ts.convertToObject(tree,errors);
    if(errors.length||!value||typeof value!=='object'||Array.isArray(value))fail('config_extends_syntax_refused');
    return value;
}

function createConfigExtendsExperiment(ts,request,expected,profiles,compilerReference){
    const admitted=admitConfigExtends(ts,request,expected,profiles,compilerReference),{policy,files,binding}=admitted;
    const observed=createConfigExtendsHost(admitted),cache=new Map();
    let disposed=false,used=false;
    return Object.freeze({replay(current){
        if(disposed)fail('config_extends_disposed');
        if(used)fail('config_extends_already_used');used=true;
        const out={version:policy.version,authority:'producer_observed_only',status:policy.refusal_status,code:'',
            binding_sha256:admitted.identity,parent_snapshot_id:binding.atlas_snapshot_id,atlas_sha256:binding.atlas_sha256,
            policy_sha256:admitted.policyIdentity,compiler_source_sha256:binding.compiler_source_sha256,context_sha256:binding.context_sha256,
            root_parse_calls:0,config_chain_calls:0,config_sourcefiles:0,programs:0,source_worker_parses:0,
            cache_entries:0,diagnostic_codes:[],edge:emptyConfigEdge(),strict_observation:emptyStrictObservation(),declared_root_sha256:null,
            reads:{root:0,base:0},host_calls:0,denied_host_calls:0,host_refusal:'',...extendsFlags};
        let rootTree;
        try{
            if(hashObject(current)!==admitted.identity||hashObject(profiles.private_owned_config_extends)!==admitted.policyIdentity
                ||configExtendsPolicyIdentity(profiles,policy)!==binding.input_policy_sha256)
                fail('config_extends_context_changed');
            const root=policy.roster.root.virtual_path,base=policy.roster.base.virtual_path;
            out.root_parse_calls++;rootTree=ts.readJsonConfigFile(root,observed.host.readFile);out.config_sourcefiles=1;
            const value=configObject(ts,rootTree,policy.max_ast_nodes);
            if(!exact(value,value.compilerOptions===undefined?['extends','files']:['extends','files','compilerOptions'])
                ||value.extends!==policy.extends_literal||canonical(value.files)!==canonical([policy.declared_source_name])
                ||value.compilerOptions!==undefined&&(!exact(value.compilerOptions,[policy.selected_option])||typeof value.compilerOptions.strict!=='boolean'))
                fail('config_extends_root_shape_refused');
            // Native code populates this empty cache. Never synthesize cache entries.
            out.config_chain_calls++;
            const parsed=ts.parseJsonSourceFileConfigFileContent(rootTree,observed.host,policy.project_virtual_root,
                undefined,root,undefined,undefined,cache);
            out.cache_entries=cache.size;
            out.config_sourcefiles=new Set([rootTree,...[...cache.values()].map(r=>r.extendedResult)]).size;
            out.diagnostic_codes=parsed.errors.map(d=>d.code);
            if(observed.summary().host_refusal)fail(observed.summary().host_refusal);
            if(parsed.errors.length)fail('config_extends_parse_diagnostics');
            const entries=[...cache.values()],entry=entries[0],baseTree=entry?.extendedResult;
            if(cache.size!==1||!baseTree||baseTree.fileName!==base||sha(baseTree.text)!==binding.files.base.host_sha256
                ||rootTree.fileName!==root||sha(rootTree.text)!==binding.files.root.host_sha256
                ||canonical(rootTree.extendedSourceFiles)!==canonical([base]))fail('config_extends_native_target_refused');
            const baseValue=configObject(ts,baseTree,policy.max_ast_nodes);
            if(!exact(baseValue,['compilerOptions'])||!exact(baseValue.compilerOptions,[policy.selected_option])
                ||typeof baseValue.compilerOptions.strict!=='boolean')fail('config_extends_base_shape_refused');
            const effective=value.compilerOptions?.strict??baseValue.compilerOptions.strict;
            if(parsed.options.strict!==effective||canonical(parsed.fileNames)!==canonical([policy.project_virtual_root+'/'+policy.declared_source_name]))
                fail('config_extends_option_observation_refused');
            const expression=rootTree.statements[0]?.expression;
            const property=expression?.properties?.find(p=>p.name?.text==='extends'),literal=property?.initializer;
            if(!literal||!ts.isStringLiteral(literal)||literal.text!==policy.extends_literal)fail('config_extends_literal_refused');
            const start=literal.getStart(rootTree),end=literal.end,position=rootTree.getLineAndCharacterOfPosition(start);
            if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||end<=start||end>rootTree.text.length)
                fail('config_extends_position_refused');
            const reads=observed.summary().reads;
            if(reads.root!==1||reads.base!==1)fail('config_extends_read_coverage_refused');
            out.edge={source_role:'root',target_role:'base',source_raw_sha256:binding.files.root.raw_sha256,
                source_host_sha256:binding.files.root.host_sha256,target_raw_sha256:binding.files.base.raw_sha256,
                target_host_sha256:binding.files.base.host_sha256,source_path_sha256:sha(root),target_path_sha256:sha(base),
                specifier_sha256:sha(literal.text),literal_host_span_utf16:{start,end},line:position.line+1,character:position.character+1};
            out.strict_observation={base:baseValue.compilerOptions.strict,root_override:value.compilerOptions?.strict??null,effective};
            out.declared_root_sha256=sha(parsed.fileNames[0]);out.status=policy.success_status;
        }catch(error){out.code=error.code||'config_extends_parser_failed';
            if(rootTree?.parseDiagnostics?.length)out.diagnostic_codes=rootTree.parseDiagnostics.map(d=>d.code);
        }finally{Object.assign(out,observed.summary());cache.clear();}
        if(Buffer.byteLength(JSON.stringify(out),'utf8')>admitted.image.limits.metadata_bytes){
            Object.assign(out,{status:policy.refusal_status,code:'config_extends_metadata_budget_refused',edge:emptyConfigEdge(),
                strict_observation:emptyStrictObservation(),declared_root_sha256:null,diagnostic_codes:[]});
        }
        return out;
    },dispose(){disposed=true;for(const row of files.values())row.raw.fill(0);files.clear();cache.clear();}});
}

module.exports = { admitDeclarationHandoff, createDeclarationExperiment, decodeCapturedConfig, inspectConfigTree,
    admitConfigExtends,createConfigExtendsHost,createConfigExtendsExperiment };
