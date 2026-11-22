'use strict';
// Controlled synthetic adapter. Not a runtime selector or alternate transport.
const mode = process.argv[4];
if (mode === 'stall') {
    process.stdin.pause(); setInterval(() => {},1000);
} else {
    const image = require('../../core/ts_input_image_experiment.cjs');
    const { performance } = require('node:perf_hooks');
    let expired = false, finished = 0;
    const finish = image._finishOwnedText;
    image._finishOwnedText = owner => {
        const result = finish(owner); finished++;
        if (mode === 'between_files' && finished === 1) expired = true;
        return result;
    };
    const gate = require('../../core/compiler_declaration_text.cjs');
    const factory = gate.createCompilerDeclarationTextSetPreparation;
    gate.createCompilerDeclarationTextSetPreparation = (...args) => {
        const options = args[5];
        args[5] = {...options,now:() => expired ? options.deadline+1 : performance.now()};
        const owner = factory(...args);
        return {...owner,prepare() {
            const prepared = owner.prepare();
            if (mode === 'after_prepare') expired = true;
            return {...prepared,consume(callback) {
                return prepared.consume(rows => {
                    if (mode === 'summary_error') throw Error('private synthetic summary error');
                    const result = callback(rows);
                    if (mode === 'after_summary') expired = true;
                    return result;
                });
            }};
        }};
    };
    const { runText } = require('../../core/compiler_stream_endpoint.cjs');
    if (mode === 'guarded') {
        for (const key of ['createExperiment','admitCompilerPackageMetadata','admitTypingPackages']) image[key] = () => { throw Error('forbidden image'); };
        const Module = require('node:module'), fs = require('node:fs'), load = Module._load;
        Module._load = function(name,...args) {
            if (!['./compiler_declaration_text.cjs','./ts_input_image_experiment.cjs'].includes(name)) throw Error('forbidden module');
            return load.call(this,name,...args);
        };
        for (const name of ['readFileSync','readFile','openSync','open','statSync','stat','readdirSync','readdir','writeFileSync','writeFile']) fs[name] = () => { throw Error('forbidden fs'); };
    }
    const write = process.stdout.write.bind(process.stdout);
    process.stdout.write = (data,...args) => {
        const row = JSON.parse(data.toString('utf8'));
        if (row.kind === 'ready' && mode === 'wrong_ready') row.version = 'foreign';
        if (row.kind === 'result') {
            if (mode === 'wrong_summary') row.prepared_text_summary_sha256 = '0'.repeat(64);
            if (mode === 'wrong_count') row.files_completed++;
            if (mode === 'wrong_type') row.files_completed = true;
            if (mode === 'wrong_ref') row.preparation_policy_sha256 = '0'.repeat(64);
            if (mode === 'raw_version') row.version = 'v1-private-synthetic-compiler-stream-driver';
            if (mode === 'promoted') row.host_text_verified = true;
            if (mode === 'extra_text') row.hostText = 'private synthetic text';
            if (mode === 'nonzero') process.exitCode = 4;
            if (mode === 'hang_result') setInterval(() => {},1000);
            if (mode === 'blocked_output') { setInterval(() => {},1000); return false; }
            if (mode === 'write_error') { args.find(value => typeof value === 'function')?.(Error('private write fault')); return false; }
            if (mode === 'trailing') {
                write(JSON.stringify(row)+'\n',...args); return write('{"extra":true}\n');
            }
        }
        return write(JSON.stringify(row)+'\n',...args);
    };
    runText().catch(() => {
        process.exitCode = 1; process.stdin.destroy(); process.stdout.destroy();
        process.stderr.end('synthetic_text_peer_refused\n');
    });
}
