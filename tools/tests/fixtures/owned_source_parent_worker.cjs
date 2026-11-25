'use strict';
// Opt-in synthetic launcher; raw text remains on the existing owned stdin protocol.
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const Module = require('node:module');
const {createCompilerCostObserver, installCompilerCostLoader} = require('./owned_source_parent_cost.cjs');
const {installCostResponseWriter} = require('./owned_source_parent_response.cjs');
const root = path.resolve(__dirname, '../../..');
let observer, hook, writer;
try {
    const args = process.argv.slice(2);
    if (args.length !== 4 || args[0] !== '--owned-text-worker-jsonl') throw Error('private_worker_args');
    const [_, invocation_id, attempt_id, compiler_source_sha256] = args;
    const lineage = JSON.parse(fs.readFileSync(path.join(root, 'config/analysis_snapshot_lineage_contract.json'), 'utf8'));
    const profiles = lineage.artifacts.ts_diagnostics, policy = profiles.private_owned_text_bridge;
    const provenance = JSON.parse(fs.readFileSync(path.join(root, 'config/third_party_distribution_contract.json'), 'utf8'));
    const owner = provenance.vendored_dependencies.find(row => row.id === 'typescript');
    const packagePath = path.join(root, owner.runtime_package_path);
    const manifest = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
    const modulePath = require.resolve('typescript', {paths: [path.join(root, 'tools/engines')]});
    if (manifest.version !== owner.locked_version || modulePath !== path.resolve(path.dirname(packagePath), manifest.main) ||
        crypto.createHash('sha256').update(fs.readFileSync(modulePath)).digest('hex') !== compiler_source_sha256)
        throw Error('private_worker_compiler_identity');
    const ts = require(modulePath);
    observer = createCompilerCostObserver(ts, {limits: {createSourceFile: 1, createProgram: 1},
        context: {invocation_id, attempt_id, compiler_source_sha256}, requestIdLimit: policy.max_request_id_chars});
    hook = installCompilerCostLoader(Module, ts, observer);
    writer = installCostResponseWriter(process.stdout, observer, hook, policy.max_response_bytes);
    process.argv = [process.execPath, path.join(root, 'tools/engines/ast_sequencer.cjs'), '--owned-text-worker-jsonl'];
    require(process.argv[1]);
} catch {
    if (writer) writer.restore();
    if (hook) hook.restore();
    if (observer) observer.dispose();
    process.exitCode = 2; // No raw error, source, frame or AST is printed.
}
