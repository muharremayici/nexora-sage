const ts = require('typescript');
const fs = require('fs');
const path = require('path');
const { Buffer } = require('buffer');

// Simple logger
const log = (msg) => console.error(`[DEBUG] ${msg}`);

function extractSignature(node, sourceFile) {
    try {
        if (ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node)) {
            const name = node.name ? node.name.getText(sourceFile) : 'anonymous';
            const params = node.parameters.map(p => p.getText(sourceFile)).join(', ');
            const returnType = node.type ? `: ${node.type.getText(sourceFile)}` : '';
            return `function ${name}(${params})${returnType}`;
        }
        return "signature-placeholder";
    } catch (e) { return ""; }
}

function analyzeFileSimple(targetFile) {
    log(`Reading file: ${targetFile}`);
    const sourceCode = fs.readFileSync(targetFile, 'utf8');
    log(`Source size: ${sourceCode.length} chars`);
    
    const startParse = Date.now();
    const sourceFile = ts.createSourceFile(targetFile, sourceCode, ts.ScriptTarget.Latest, true);
    log(`Parsed AST in ${Date.now() - startParse}ms`);

    const symbols = [];
    let visitCount = 0;

    function visit(node) {
        visitCount++;
        if (visitCount % 1000 === 0) log(`Visited ${visitCount} nodes...`);

        if (ts.isSourceFile(node)) {
            ts.forEachChild(node, visit);
            return;
        }

        const isCandidate = (ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node) ||
                            ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node) ||
                            ts.isExportAssignment(node) || ts.isVariableStatement(node) ||
                            ts.isExportDeclaration(node));

        if (isCandidate) {
            log(`Found Candidate: ${ts.SyntaxKind[node.kind]} at ${node.getStart(sourceFile)}`);
            // Minimal processing
            symbols.push({ kind: ts.SyntaxKind[node.kind], start: node.getStart(sourceFile) });
        }

        ts.forEachChild(node, visit);
    }

    const startVisit = Date.now();
    visit(sourceFile);
    log(`Visited total ${visitCount} nodes in ${Date.now() - startVisit}ms`);

    return symbols;
}

const target = process.argv[2];
if (!target || !fs.existsSync(target)) {
    console.error("Path required");
    process.exit(1);
}

try {
    const results = analyzeFileSimple(target);
    console.log(JSON.stringify({ symbolCount: results.length }));
} catch (err) {
    console.error(err);
}
