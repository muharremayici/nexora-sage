'use strict';
// Shared lexical formatting only. The caller owns the compiler and SourceFile.
const path = require('node:path');

function diagnosticPayload(ts, diag, projectRoot) {
    const message = ts.flattenDiagnosticMessageText(diag.messageText, '\n');
    let file = '', line = 1, character = 1;
    if (diag.file) {
        file = String(path.relative(projectRoot, diag.file.fileName) || '').replace(/\\/g, '/');
        const pos = diag.file.getLineAndCharacterOfPosition(diag.start || 0);
        line = pos.line + 1;
        character = pos.character + 1;
    }
    return {code: `TS${diag.code}`, category: ts.DiagnosticCategory[diag.category] || 'Unknown',
        file, line, character, message};
}

module.exports = { diagnosticPayload };
