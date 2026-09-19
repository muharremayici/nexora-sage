const fs = require("fs");
const path = require("path");
const ts = require("typescript");

function readJson(filePath, fallback = {}) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

function normalizePath(value) {
  return String(value || "").replace(/\\/g, "/");
}

function isPathWithin(root, candidate) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function isResolvedPathWithin(root, candidate) {
  if (!isPathWithin(root, candidate)) return false;
  try {
    const realRoot = fs.realpathSync.native(path.resolve(root));
    const realCandidate = fs.realpathSync.native(path.resolve(candidate));
    return isPathWithin(realRoot, realCandidate);
  } catch {
    return false;
  }
}

function boundedParseHost(workspaceRoot) {
  const allow = (candidate) => isResolvedPathWithin(workspaceRoot, candidate);
  return {
    ...ts.sys,
    fileExists: (candidate) => allow(candidate) && ts.sys.fileExists(candidate),
    readFile: (candidate, encoding) => allow(candidate) ? ts.sys.readFile(candidate, encoding) : undefined,
    directoryExists: (candidate) => allow(candidate) && ts.sys.directoryExists(candidate),
    getDirectories: (candidate) => allow(candidate) ? ts.sys.getDirectories(candidate) : [],
    readDirectory: (candidate, extensions, excludes, includes, depth) => (
      allow(candidate)
        ? ts.sys.readDirectory(candidate, extensions, excludes, includes, depth).filter((item) => allow(item))
        : []
    ),
    realpath: (candidate) => allow(candidate) ? ts.sys.realpath(candidate) : path.resolve(workspaceRoot),
  };
}

function boundedCompilerHost(options, workspaceRoot) {
  const host = ts.createCompilerHost(options, true);
  const typescriptLibRoot = path.dirname(require.resolve("typescript"));
  const allow = (candidate) => (
    isResolvedPathWithin(workspaceRoot, candidate)
    || isResolvedPathWithin(typescriptLibRoot, candidate)
  );
  const originalFileExists = host.fileExists.bind(host);
  const originalReadFile = host.readFile.bind(host);
  const originalDirectoryExists = host.directoryExists ? host.directoryExists.bind(host) : undefined;
  const originalGetDirectories = host.getDirectories ? host.getDirectories.bind(host) : undefined;
  const originalGetSourceFile = host.getSourceFile.bind(host);
  const originalRealpath = host.realpath ? host.realpath.bind(host) : undefined;
  return {
    ...host,
    fileExists: (candidate) => allow(candidate) && originalFileExists(candidate),
    readFile: (candidate) => allow(candidate) ? originalReadFile(candidate) : undefined,
    directoryExists: (candidate) => (
      allow(candidate) && (originalDirectoryExists ? originalDirectoryExists(candidate) : false)
    ),
    getDirectories: (candidate) => (
      allow(candidate) && originalGetDirectories
        ? originalGetDirectories(candidate).filter((item) => allow(item))
        : []
    ),
    getSourceFile: (fileName, languageVersion, onError, shouldCreateNewSourceFile) => (
      allow(fileName)
        ? originalGetSourceFile(fileName, languageVersion, onError, shouldCreateNewSourceFile)
        : undefined
    ),
    realpath: (candidate) => (
      allow(candidate) && originalRealpath ? originalRealpath(candidate) : path.resolve(workspaceRoot)
    ),
  };
}

function boundedCompilerOptions(options) {
  const bounded = { ...options, noEmit: true, skipLibCheck: true, incremental: false, composite: false };
  for (const key of ["paths", "plugins", "rootDirs", "typeRoots", "tsBuildInfoFile"]) {
    delete bounded[key];
  }
  bounded.baseUrl = undefined;
  return bounded;
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key.startsWith("--")) {
      args[key.slice(2)] = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
    }
  }
  return args;
}

function diagnosticPayload(diag, projectRoot) {
  const message = ts.flattenDiagnosticMessageText(diag.messageText, "\n");
  let file = "";
  let line = 1;
  let character = 1;
  if (diag.file) {
    file = normalizePath(path.relative(projectRoot, diag.file.fileName));
    const pos = diag.file.getLineAndCharacterOfPosition(diag.start || 0);
    line = pos.line + 1;
    character = pos.character + 1;
  }
  return {
    code: `TS${diag.code}`,
    category: ts.DiagnosticCategory[diag.category] || "Unknown",
    file,
    line,
    character,
    message,
  };
}

function collectForProject(project, projectRoot, workspaceRoot, maxDiagnostics, maxFiles, semantic) {
  const tsconfig = path.join(projectRoot, "tsconfig.json");
  if (!isResolvedPathWithin(projectRoot, tsconfig) || !ts.sys.fileExists(tsconfig)) {
    return {
      project,
      project_root: normalizePath(projectRoot),
      status: "NO_TSCONFIG",
      tsconfig: "",
      diagnostics: [],
      summary: { total: 0, by_code: {}, by_category: {} },
    };
  }

  const parseHost = boundedParseHost(workspaceRoot);
  const configFile = ts.readConfigFile(tsconfig, parseHost.readFile);
  if (configFile.error) {
    const diag = diagnosticPayload(configFile.error, projectRoot);
    return {
      project,
      project_root: normalizePath(projectRoot),
      status: "CONFIG_ERROR",
      tsconfig: normalizePath(path.relative(projectRoot, tsconfig)),
      diagnostics: [diag],
      summary: { total: 1, by_code: { [diag.code]: 1 }, by_category: { [diag.category]: 1 } },
    };
  }

  const parsed = ts.parseJsonConfigFileContent(configFile.config, parseHost, path.dirname(tsconfig), {
    noEmit: true,
    skipLibCheck: true,
    incremental: false,
    composite: false,
  });
  parsed.options = boundedCompilerOptions(parsed.options);
  const containedRootNames = parsed.fileNames.filter((fileName) => isResolvedPathWithin(workspaceRoot, fileName));
  const rootNames = maxFiles > 0 ? containedRootNames.slice(0, maxFiles) : containedRootNames;
  if (!semantic) {
    const diagnostics = [];
    for (const fileName of rootNames) {
      const content = parseHost.readFile(fileName);
      if (typeof content !== "string") continue;
      const sourceFile = ts.createSourceFile(fileName, content, parsed.options.target || ts.ScriptTarget.Latest, true);
      diagnostics.push(...sourceFile.parseDiagnostics);
      if (diagnostics.length >= maxDiagnostics) break;
    }
    const rows = diagnostics.slice(0, maxDiagnostics).map((diag) => diagnosticPayload(diag, projectRoot));
    const byCode = {};
    const byCategory = {};
    for (const row of rows) {
      byCode[row.code] = (byCode[row.code] || 0) + 1;
      byCategory[row.category] = (byCategory[row.category] || 0) + 1;
    }
    return {
      project,
      project_root: normalizePath(projectRoot),
      status: "OK_SYNTAX_ONLY",
      mode: "syntax",
      tsconfig: normalizePath(path.relative(projectRoot, tsconfig)),
      diagnostics_truncated: diagnostics.length > rows.length,
      diagnostics: rows,
      summary: {
        total: diagnostics.length,
        emitted: rows.length,
        by_code: byCode,
        by_category: byCategory,
        root_files_total: containedRootNames.length,
        root_files_checked: rootNames.length,
      },
    };
  }

  const program = ts.createProgram({
    rootNames,
    options: parsed.options,
    host: boundedCompilerHost(parsed.options, workspaceRoot),
  });
  const diagnostics = [
    ...program.getOptionsDiagnostics(),
    ...program.getGlobalDiagnostics(),
    ...program.getSyntacticDiagnostics(),
    ...program.getSemanticDiagnostics(),
  ];
  const rows = diagnostics.slice(0, maxDiagnostics).map((diag) => diagnosticPayload(diag, projectRoot));
  const byCode = {};
  const byCategory = {};
  for (const row of diagnostics.map((diag) => diagnosticPayload(diag, projectRoot))) {
    byCode[row.code] = (byCode[row.code] || 0) + 1;
    byCategory[row.category] = (byCategory[row.category] || 0) + 1;
  }
  return {
    project,
    project_root: normalizePath(projectRoot),
    status: "OK",
    mode: "semantic",
    tsconfig: normalizePath(path.relative(projectRoot, tsconfig)),
    diagnostics_truncated: diagnostics.length > rows.length,
    diagnostics: rows,
    summary: {
      total: diagnostics.length,
      emitted: rows.length,
      by_code: byCode,
      by_category: byCategory,
      source_files: program.getSourceFiles().filter(
        (file) => !file.isDeclarationFile && isResolvedPathWithin(workspaceRoot, file.fileName),
      ).length,
      root_files_total: containedRootNames.length,
      root_files_checked: rootNames.length,
    },
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const codeMapsDir = path.resolve(__dirname, "..", "..");
  const configPath = path.resolve(args.config || path.join(codeMapsDir, "config", "codemaps.config.json"));
  const outputPath = path.resolve(args.out || path.join(codeMapsDir, "output", ".raw", "ts_diagnostics.json"));
  const maxDiagnostics = Number(args.maxDiagnostics || 300);
  const maxFiles = Number(args.maxFiles || 260);
  const semantic = String(args.semantic || "false").toLowerCase() === "true";
  const config = readJson(configPath, {});
  const workspaceRoot = path.resolve(codeMapsDir, config.workspace_root || "..");
  const variations = config.variations || {};
  const projects = {};
  const requestedProjects = String(args.projects || "")
    .split(",")
    .map((value) => value.trim().toUpperCase())
    .filter(Boolean);
  const allowedProjects = new Set(requestedProjects);

  for (const [project, relPath] of Object.entries(variations)) {
    if (allowedProjects.size > 0 && !allowedProjects.has(String(project).toUpperCase())) {
      continue;
    }
    const projectRoot = path.resolve(workspaceRoot, relPath || ".");
    if (!isResolvedPathWithin(workspaceRoot, projectRoot)) {
      projects[project] = {
        project,
        project_root: normalizePath(projectRoot),
        status: "BOUNDARY_REJECTED",
        error: "configured_project_root_escapes_workspace",
        diagnostics: [],
        summary: { total: 0, by_code: {}, by_category: {} },
      };
      continue;
    }
    try {
      projects[project] = collectForProject(
        project,
        projectRoot,
        workspaceRoot,
        maxDiagnostics,
        maxFiles,
        semantic,
      );
    } catch (error) {
      projects[project] = {
        project,
        project_root: normalizePath(projectRoot),
        status: "COLLECTOR_ERROR",
        error: String(error && error.message ? error.message : error),
        diagnostics: [],
        summary: { total: 0, by_code: {}, by_category: {} },
      };
    }
  }

  const summary = {
    projects: Object.keys(projects).length,
    total_diagnostics: Object.values(projects).reduce((sum, item) => sum + Number(item.summary?.total || 0), 0),
    projects_with_errors: Object.values(projects).filter((item) => Number(item.summary?.total || 0) > 0).length,
    by_status: Object.values(projects).reduce((acc, item) => {
      acc[item.status] = (acc[item.status] || 0) + 1;
      return acc;
    }, {}),
  };

  const payload = {
    meta: {
      kind: "ts_diagnostics",
      version: "v1",
      compiler_version: ts.version,
      execution_scope: {
        requested_projects: requestedProjects.length > 0 ? requestedProjects : Object.keys(variations),
        analyzed_projects: Object.keys(projects),
        preserved_only_projects: Object.keys(variations).filter(
          (project) => !Object.prototype.hasOwnProperty.call(projects, project),
        ),
        unavailable_requested_projects: requestedProjects.filter(
          (project) => !Object.keys(variations).some((candidate) => candidate.toUpperCase() === project),
        ),
        aggregation_scope: "runtime_project_projection",
      },
    },
    summary,
    projects,
  };
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  process.stdout.write(JSON.stringify({ ok: true, summary }) + "\n");
}

main();
