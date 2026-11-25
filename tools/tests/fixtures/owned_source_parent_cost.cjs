'use strict';
// Explicit private observation seam. Loading this module installs no hook.
const fail = code => { const error = new Error(code); error.code = code; throw error; };
function createCompilerCostObserver(target, options) {
    const { limits, context, requestIdLimit } = options || {};
    const names = ['createSourceFile', 'createProgram'];
    if (!target || !limits || Object.keys(limits).sort().join(',') !== 'createProgram,createSourceFile'
        || !names.every(name => Number.isSafeInteger(limits[name]) && limits[name] >= 0)
        || !Number.isSafeInteger(requestIdLimit) || requestIdLimit < 1
        || !context || Object.keys(context).sort().join(',') !== 'attempt_id,compiler_source_sha256,invocation_id'
        || typeof context.compiler_source_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(context.compiler_source_sha256)
        || !['attempt_id','invocation_id'].every(key => typeof context[key] === 'string'
            && /^[A-Za-z0-9_-]+$/.test(context[key]) && context[key].length <= requestIdLimit)) fail('counter_context_refused');
    const heldContext = Object.freeze({...context}), heldLimits = Object.freeze({...limits});
    const originals = new Map(), wrapped = new Map(), counts = {};
    let disposed = false;
    for (const name of names) {
        const descriptor = Object.getOwnPropertyDescriptor(target, name), fn = Reflect.get(target, name, target);
        if (typeof fn !== 'function') fail('counter_api_unavailable');
        if (descriptor && descriptor.configurable === false && 'value' in descriptor && descriptor.writable === false)
            fail('counter_api_not_wrappable');
        originals.set(name, fn);
        counts[name] = {attempted:0,forwarded:0,completed:0,thrown:0,denied:0};
    }
    let proxy;
    for (const name of names) wrapped.set(name, function(...args) {
        const row = counts[name]; row.attempted++;
        if (disposed) { row.denied++; fail('counter_disposed'); }
        if (Reflect.get(target, name, target) !== originals.get(name)) { row.denied++; fail('counter_api_changed'); }
        if (row.forwarded >= heldLimits[name]) { row.denied++; fail('counter_budget_exceeded'); }
        row.forwarded++;
        try {
            const result = Reflect.apply(originals.get(name), this === proxy ? target : this, args);
            row.completed++; return result;
        } catch (error) { row.thrown++; throw error; }
    });
    proxy = new Proxy(target, {get(object, key) {
        return wrapped.has(key) ? wrapped.get(key) : Reflect.get(object, key, object);
    }});
    return Object.freeze({api:proxy, snapshot() {
        return {version:'v1-outer-compiler-api-cost',context:{...heldContext},
            basis:'outer_api_calls_only',native_execution_attested:false,disposed,
            counts:Object.fromEntries(names.map(name => [name,{...counts[name]}]))};
    }, dispose() { disposed = true; }});
}

function installCompilerCostLoader(loader, target, observer) {
    const original = loader && loader._load;
    if (typeof original !== 'function' || !observer || !observer.api) fail('counter_loader_refused');
    function wrapped(...args) {
        const result = Reflect.apply(original, this, args);
        return result === target ? observer.api : result;
    }
    try { loader._load = wrapped; } catch { fail('counter_loader_refused'); }
    if (loader._load !== wrapped) fail('counter_loader_refused');
    let restored = false;
    return Object.freeze({restore() {
        if (restored) return true;
        if (loader._load !== wrapped) return false; // Preserve another owner's hook.
        try { loader._load = original; } catch { return false; }
        if (loader._load !== original) return false;
        restored = true; return true;
    }});
}
module.exports = {createCompilerCostObserver, installCompilerCostLoader};
