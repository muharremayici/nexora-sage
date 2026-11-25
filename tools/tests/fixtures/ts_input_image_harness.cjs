'use strict';
// Existing native fixture process; no production entrypoint or raw-image file.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../../..');
const tsPath = require.resolve('typescript', { paths: [path.join(root, 'tools/engines')] });
const compilerSourceReference = crypto.createHash('sha256').update(fs.readFileSync(tsPath)).digest('hex');
const ts = require(tsPath);
const { createExperiment, hashObject } = require('../../core/ts_input_image_experiment.cjs');
const { createRequire } = require('node:module');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const project = '/__sage_ts_image__/project', compiler = '/__sage_ts_image__/compiler';
const mode = process.argv[2];
const policy = JSON.parse(fs.readFileSync(path.join(root, 'config/analysis_snapshot_lineage_contract.json'), 'utf8'))
    .artifacts.ts_diagnostics.private_input_image_experiment;
const sources = {
    'a.ts': '\ufeffimport { value } from "./b";\r\nexport const count: number = value; // \ud83e\uddea\r\n',
    'b.ts': 'export const value = "SAGE_SYNTHETIC_PRIVATE_MARKER";\n',
    'view.tsx': 'export const view = <div />;\n', 'empty.ts': '',
};
if (mode === 'parser_clean' || mode === 'clean') sources['a.ts'] = sources['a.ts'].replace('count: number', 'count: string');
const entries = Object.entries(sources).map(([name, text]) => ({ path: project + '/' + name, role: 'source', raw: Buffer.from(text) }));
entries.push({ path: project + '/jsx.d.ts', role: 'auxiliary', raw: Buffer.from('declare namespace JSX { interface IntrinsicElements { div: {}; } }\n') });
const config = { files: [...Object.keys(sources), 'jsx.d.ts'], compilerOptions: policy.compiler_options };

if (mode === 'parser' || mode === 'parser_clean') {
    // Feed exact owner-held bytes into the UNCHANGED native parser's actual
    // readFileSync call. Its module/config/toolchain reads retain their owner.
    const original = fs.readFileSync, stat = fs.statSync, write = process.stdout.write;
    let output = '';
    fs.readFileSync = function (candidate, encoding) {
        const row = entries.find(entry => entry.path === String(candidate));
        if (row) return encoding === 'utf8' ? row.raw.toString('utf8') : Buffer.from(row.raw);
        return original.apply(this, arguments);
    };
    fs.statSync = function (candidate) {
        const row = entries.find(entry => entry.path === String(candidate));
        return row ? { mtimeMs: 0, size: row.raw.length, isFile: () => true } : stat.apply(this, arguments);
    };
    process.stdout.write = chunk => { output += String(chunk); return true; };
    const parser = path.join(root, 'tools/engines/ast_sequencer.cjs');
    process.argv = [process.execPath, parser, '--batch-json', ...entries.filter(row => row.role === 'source').map(row => row.path)];
    try { createRequire(parser)(parser); }
    finally { fs.readFileSync = original; fs.statSync = stat; process.stdout.write = write; }
    process.stdout.write(JSON.stringify({ sources, raw: JSON.parse(output), policy_sha256: hashObject(policy),
        compiler_version: ts.version, compiler_source_sha256: compilerSourceReference }));
} else {
    const binding = JSON.parse(process.argv[3]);
    const snapshot = binding.atlas_snapshot_id;
    const modifications = {
        file_budget: ['source_file_bytes', 1], count_budget: ['source_files', 1],
        content_budget: ['source_bytes', 1], directory_budget: ['directories', 1],
        names_budget: ['directory_name_bytes', 1], entries_budget: ['directory_entries', 1],
        metadata_budget: ['metadata_bytes', 1], image_budget: ['image_bytes', 1],
        host_budget: ['host_calls', 1], rss_budget: ['rss_observation_bytes', 1],
    };
    if (modifications[mode]) { const [key, value] = modifications[mode]; policy.limits[key] = value; }
    if (mode === 'count_at_limit') policy.limits.source_files = Object.keys(sources).length;
    if (mode === 'capture_timeout') policy.limits.capture_ms = 1;
    if (mode === 'replay_timeout') policy.limits.replay_ms = 1;
    if (mode === 'bad_policy') policy.production_enabled = true;
    if (mode === 'unknown_policy') policy.version = 'future';
    if (mode === 'bad_number') policy.limits.host_calls = true;
    if (mode === 'unsupported_config') config.extends = './outside.json';
    if (mode === 'wrong_options') config.compilerOptions = { ...config.compilerOptions, noEmit: false };
    entries.push({ path: project + '/tsconfig.json', role: 'auxiliary', raw: Buffer.from(JSON.stringify(config)) });
    const library = path.dirname(tsPath);
    const names = fs.readdirSync(library).filter(name => /^lib[^/]*\.d\.ts$/.test(name)).sort();
    if (names.length > policy.limits.compiler_files) throw new Error('fixture_library_inventory_over_budget');
    let libraryBytes = 0;
    // Fixture preparation, not a new production source capture owner. Bound
    // the actual retained read, including a sentinel, before factory admission.
    for (const name of names) {
        const fd = fs.openSync(path.join(library, name), 'r');
        let raw;
        try {
            const ceiling = Math.min(policy.limits.compiler_file_bytes,
                policy.limits.compiler_bytes - libraryBytes);
            if (ceiling < 0) throw new Error('fixture_library_bytes_over_budget');
            const size = fs.fstatSync(fd).size;
            if (size > ceiling) throw new Error('fixture_library_bytes_over_budget');
            const buffer = Buffer.alloc(size + 1);
            let count = 0, read;
            while (count < buffer.length && (read = fs.readSync(fd, buffer, count, buffer.length - count, null))) count += read;
            if (count > size) throw new Error('fixture_library_changed_or_over_budget');
            raw = buffer.subarray(0, count);
        } finally { fs.closeSync(fd); }
        libraryBytes += raw.length;
        entries.push({ path: compiler + '/' + name, role: 'compiler', raw });
    }
    if (mode === 'bad_encoding') entries[0].raw = Buffer.from([0xff]);
    if (mode === 'tampered_source') entries[0].raw = Buffer.from('export const other = 1;');
    if (mode === 'escaping_import' || mode === 'bare_import') {
        entries[0].raw = Buffer.from(mode === 'escaping_import' ? 'import "../../../outside";' : 'import "react";');
        binding.source_sha256[entries[0].path] = sha(entries[0].raw);
    }
    if (mode === 'missing_blob') entries[0].raw = undefined;
    if (mode === 'bad_path') entries[0].path = project + '/../a.ts';
    if (mode === 'case_collision') entries.push({ ...entries[0], path: project + '/A.ts' });
    if (mode === 'directory_case_collision') {
        entries.push({ path: project + '/X/a.ts', role: 'source', raw: Buffer.from('') });
        entries.push({ path: project + '/x/b.ts', role: 'source', raw: Buffer.from('') });
    }
    if (mode === 'missing_root') entries.splice(entries.findIndex(row => row.path === project + '/empty.ts'), 1);
    if (mode === 'missing_default_lib') entries.splice(entries.findIndex(row => row.path === compiler + '/lib.es2020.full.d.ts'), 1);
    binding.policy_sha256 = hashObject(policy);
    if (mode === 'wrong_compiler') binding.compiler_version = 'unknown';
    if (mode === 'wrong_compiler_reference') binding.compiler_source_sha256 = 'f'.repeat(64);
    let instance, output, fallbackAttempts = 0;
    const restorations = [];
    function poison(owner, key) {
        if (typeof owner[key] !== 'function') return;
        const original = owner[key];
        owner[key] = () => { fallbackAttempts++; const error = new Error('poisoned_fs_attempt'); error.code = 'poisoned_fs_attempt'; throw error; };
        restorations.push(() => { owner[key] = original; });
    }
    let api = ts;
    if (mode.startsWith('host_')) {
        // Failure injection at the real compiler API boundary, not a hidden
        // production switch or a claim that the native compiler made this call.
        api = Object.create(ts);
        Object.defineProperty(api, 'createProgram', { value: options => {
            const host = options.host;
            if (mode === 'host_write') host.writeFile(project + '/out.js', 'never');
            if (mode === 'host_enumeration') host.readDirectory(project);
            if (mode === 'host_realpath') host.realpath(project + '/not-present');
            if (mode === 'host_drive') host.readFile('C:/not-an-image-input');
            if (mode === 'host_absence') {
                if (host.fileExists('/node_modules/available-on-host.ts') !== false
                    || host.readFile(project + '/not-present.ts') !== undefined
                    || host.directoryExists('/outside-fixture') !== false)
                    throw new Error('fixture_virtual_absence_failed');
            }
            return ts.createProgram(options);
        } });
    }
    try {
        if (binding.compiler_source_sha256 !== compilerSourceReference) {
            const error = new Error('preload_source_reference_mismatch'); error.code = 'preload_source_reference_mismatch'; throw error;
        }
        instance = createExperiment(api, { entries, namespace_complete: mode !== 'partial_namespace' }, binding, policy);
        const expected = JSON.parse(JSON.stringify(binding));
        if (mode === 'wrong_atlas') expected.atlas_snapshot_id = 'f'.repeat(64);
        if (mode === 'wrong_run') expected.producer_run_id = 'another';
        if (mode === 'wrong_policy_binding') expected.policy_sha256 = 'f'.repeat(64);
        if (mode === 'mutated_owner_buffer') entries[0].raw.fill(0);
        if (mode === 'disposed') instance.dispose();
        for (const key of ['readFileSync', 'statSync', 'readdirSync', 'existsSync', 'realpathSync', 'openSync']) poison(fs, key);
        for (const key of ['readFile', 'fileExists', 'directoryExists', 'readDirectory', 'getDirectories', 'realpath']) poison(ts.sys, key);
        if (mode === 'poison_probe') fs.readFileSync('/fixture-only-probe');
        output = instance.replay(expected);
        output.fallback_attempts = fallbackAttempts;
        output.requested_atlas_snapshot_id = snapshot;
    } catch (error) {
        output = { status: 'REFUSED', code: error.code || 'unexpected_error',
            fallback_attempts: fallbackAttempts, raw_image_persisted: false };
        if (!error.code) output.unexpected_type = error.constructor.name;
    } finally {
        for (const restore of restorations.reverse()) restore();
        if (instance) instance.dispose();
    }
    process.stdout.write(JSON.stringify(output));
}
