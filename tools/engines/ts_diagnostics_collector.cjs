const fs = require("fs");
const path = require("path");
let ts;
let fileSystemObservation;
const crypto = require("crypto");

function checkedSource(fileName, text, projectRoot) {
  return {
    file: normalizePath(path.relative(projectRoot, fileName)),
    md5: crypto.createHash("md5").update(text, "utf8").digest("hex"),
    sha256: crypto.createHash("sha256").update(text, "utf8").digest("hex"),
  };
}

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
  return relative === "" || (relative !== ".." && !relative.startsWith(".." + path.sep) && !path.isAbsolute(relative));
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

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item) ?? "null").join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).filter((key) => value[key] !== undefined).sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function contextHash(value) {
  return crypto.createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}

let observedCompiler;
function observedCompilerIdentity() {
  if (!observedCompiler) {
    observedCompiler = {
      authority: "collector_toolchain_observation",
      version: ts.version, node_version: process.version,
      library_root: normalizePath(path.dirname(require.resolve("typescript"))),
      installed_module: normalizePath(require.resolve("typescript")),
      module_hash_basis: "installed_entry_bytes_read_before_analysis",
      installed_module_sha256: crypto.createHash("sha256")
        .update(fs.readFileSync(require.resolve("typescript"))).digest("hex"),
    };
  }
  return observedCompiler;
}

function semanticContextRecorder(limit, directoryPolicy = null) {
  const observations = new Map();
  const stream = crypto.createHash("sha256");
  let calls = 0;
  let omitted = 0;
  let inputUnits = 0;
  let inputEntries = 0;
  let inputNameBytes = 0;
  const accessCounts = {};
  return {
    beginDirectoryQuery() {
      const inputs = [];
      let inputCalls = 0, skipped = 0;
      const omit = () => { skipped += 1; omitted += 1; };
      return {
        observe(operation, args, result) {
          inputCalls += 1;
          if (!directoryPolicy || observations.size + inputUnits >= limit) { omit(); return; }
          let value = null;
          if (operation === "readdirSync" && Array.isArray(result)) {
            if (inputEntries + result.length > directoryPolicy.max_entries) { omit(); return; }
            const files = [], directories = [];
            let unsupported = 0;
            for (const entry of result) {
              const name = typeof entry === "string" ? entry : entry.name;
              if (typeof name !== "string") { omit(); return; }
              inputNameBytes += Buffer.byteLength(name, "utf8");
              if (inputNameBytes > directoryPolicy.max_name_bytes) { omit(); return; }
              if (typeof entry === "string" || entry.isSymbolicLink()) unsupported += 1;
              else if (entry.isFile()) files.push(name);
              else if (entry.isDirectory()) directories.push(name);
              else unsupported += 1;
            }
            inputEntries += result.length;
            const digest = names => ({ entries: names.length, sha256: contextHash(names.sort()) });
            value = { entries: result.length, files: digest(files),
              directories: digest(directories), unsupported_entries: unsupported };
          } else if (["realpathSync", "realpathSync.native"].includes(operation) && typeof result === "string") {
            value = normalizePath(path.resolve(result));
          }
          // Stat-followed links, legacy string listings and other input kinds
          // remain explicit unsupported observations, never inferred as files.
          const row = { operation, path: normalizePath(path.resolve(args[0])), value };
          inputs.push(row);
          inputUnits += 1;
        },
        finish(failed) {
          return { version: "v1", scope: "actual_read_directory_calls",
            complete: !failed && skipped === 0, observed_calls: inputCalls,
            omitted_observations: skipped, observations: inputs };
        },
      };
    },
    observe(stage, operation, candidate, args, access, result, filteredEntries = 0, recursiveInputs = null) {
      // Store identities, never source/config contents. Hash the returned text,
      // not a second filesystem read that could observe different input.
      const value = operation === "readFile" && typeof result === "string"
        ? { text_md5: crypto.createHash("md5").update(result, "utf8").digest("hex"),
            text_sha256: crypto.createHash("sha256").update(result, "utf8").digest("hex") }
        : Array.isArray(result)
          ? { entries: result.length, sha256: contextHash(result) }
          : result === undefined ? null : result;
      const row = { stage, operation, path: normalizePath(path.resolve(candidate)),
        arguments_sha256: contextHash(args), allowed: access.allowed,
        access_status: access.status, input_scope: access.scope,
        error_code: access.error_code || null, filtered_entries: filteredEntries, value };
      if (access.io_failure) row.io_failure = access.io_failure;
      if (recursiveInputs) row.recursive_inputs = recursiveInputs;
      accessCounts[access.status] = (accessCounts[access.status] || 0) + 1;
      const identity = contextHash(row);
      stream.update(identity + "\n");
      calls += 1;
      if (!observations.has(identity)) {
        if (observations.size + inputUnits < limit) observations.set(identity, row);
        else omitted += 1;
      }
    },
    finish(metadata) {
      if (omitted) metadata.binding_blockers.push("context_observation_budget_exceeded");
      if (accessCounts.unavailable || accessCounts.changed_during_probe) {
        metadata.binding_blockers.push("host_access_unavailable");
      }
      const identity = { ...metadata, access_counts: accessCounts, observations_sha256: stream.digest("hex") };
      return {
        version: "v1", authority: "observation_only", snapshot_bound: false,
        native_project_equivalent: false,
        path_basis: "normalized_absolute_host_paths",
        hash_basis: "sha256_utf8_host_text_and_canonical_json_observations",
        ...identity, context_sha256: contextHash(identity),
        observation_limit: limit, observed_calls: calls, omitted_observations: omitted,
        input_observation_units: inputUnits,
        observations_complete: omitted === 0,
        observations: [...observations.values()],
      };
    },
  };
}

function boundedPathAccess(roots) {
  const boundaries = roots.map(({ root, scope }) => ({
    root: path.resolve(root), realRoot: fs.realpathSync.native(path.resolve(root)), scope,
  }));
  return (candidate) => {
    const absolute = path.resolve(candidate);
    const boundary = boundaries.find((entry) => isPathWithin(entry.root, absolute));
    if (!boundary) return { allowed: false, status: "outside_boundary", scope: "outside" };
    let cursor = absolute;
    let missing = false;
    while (true) {
      try {
        const resolved = fs.realpathSync.native(cursor);
        if (!isPathWithin(boundary.realRoot, resolved)) {
          return { allowed: false, status: "outside_boundary", scope: boundary.scope };
        }
        return { allowed: true, status: missing ? "missing" : "present", scope: boundary.scope };
      } catch (error) {
        if (error.code !== "ENOENT") {
          return { allowed: false, status: "unavailable", scope: boundary.scope, error_code: error.code || "UNKNOWN" };
        }
        // A dangling link is not an absent in-boundary file. Never follow its
        // prospective target, or climb past a link and mislabel it as absence.
        try {
          if (fs.lstatSync(cursor).isSymbolicLink()) {
            return { allowed: false, status: "unavailable", scope: boundary.scope, error_code: "UNRESOLVED_LINK" };
          }
        } catch (statError) {
          if (statError.code !== "ENOENT") {
            return { allowed: false, status: "unavailable", scope: boundary.scope, error_code: statError.code || "UNKNOWN" };
          }
        }
        if (path.relative(boundary.root, cursor) === "" || path.dirname(cursor) === cursor) {
          return { allowed: false, status: "unavailable", scope: boundary.scope, error_code: "BOUNDARY_MISSING" };
        }
        missing = true;
        cursor = path.dirname(cursor);
      }
    }
  };
}

function observeFileSystemFailures() {
  // This collector is a synchronous, isolated CLI. Install before loading TS:
  // on POSIX it captures fs.realpathSync.native, not a later property lookup.
  // Wrappers observe existing calls only and are restored when the CLI exits.
  let active = null;
  const restore = [];
  function recordFailure(operation, code) {
    if (!active) return;
    active.count += 1;
    // A later permission/I/O failure must not hide behind earlier absence.
    if (!active.failure || active.failure.code === "ENOENT") active.failure = { operation, code };
  }
  function wrap(owner, key, operation) {
    const original = owner[key];
    const wrapped = function (...args) {
      try {
        if (operation === "readdirSync" && active?.directoryAccess) {
          // TS recursively enumerates inside one readDirectory call. Filtering
          // its returned files is too late to protect linked directories.
          const scope = active;
          let access;
          active = null; // Boundary probes are not compiler input/failure calls.
          try { access = scope.directoryAccess(args[0]); } finally { active = scope; }
          if (!access.allowed) {
            const error = new Error("Compiler directory access rejected");
            error.code = access.status === "outside_boundary"
              ? "SAGE_BOUNDARY_DENIED" : access.error_code || "HOST_DIRECTORY_UNAVAILABLE";
            throw error;
          }
        }
        const value = original.apply(this, args);
        // Node's throwIfNoEntry:false stat also hides disappearance without
        // throwing. Preserve that observation without probing the path again.
        if (operation === "statSync" && value === undefined) recordFailure(operation, "ENOENT");
        if (active?.inputObserver) active.inputObserver.observe(operation, args, value);
        return value;
      } catch (error) {
        recordFailure(operation, typeof error?.code === "string" ? error.code : "HOST_IO_UNKNOWN");
        throw error;
      }
    };
    owner[key] = wrapped;
    restore.push(() => { owner[key] = original; });
    return wrapped;
  }
  try {
    for (const operation of ["readFileSync", "statSync", "readdirSync"]) wrap(fs, operation, operation);
    const native = fs.realpathSync.native;
    const realpath = wrap(fs, "realpathSync", "realpathSync");
    realpath.native = native;
    if (typeof native === "function") wrap(realpath, "native", "realpathSync.native");
  } catch (error) {
    for (const undo of restore.reverse()) undo();
    throw error;
  }
  return {
    run(callback, directoryAccess = null, inputObserver = null) {
      const parent = active;
      const current = { count: 0, failure: null, directoryAccess, inputObserver };
      active = current;
      try {
        const value = callback();
        return { value, count: current.count, failure: current.failure };
      } finally {
        active = parent;
        if (parent && current.failure) {
          parent.count += current.count;
          if (!parent.failure || parent.failure.code === "ENOENT") parent.failure = current.failure;
        }
      }
    },
    restore() {
      for (const undo of restore.reverse()) undo();
      restore.length = 0;
    },
  };
}

function observeHost(host, stage, recorder, accessFor, workspaceRoot, accessState = null) {
  for (const operation of ["fileExists", "readFile", "directoryExists", "getDirectories", "readDirectory", "realpath"]) {
    const original = host[operation];
    if (typeof original !== "function") continue;
    host[operation] = (candidate, ...args) => {
      let access = accessFor(candidate);
      const directoryTrace = operation === "readDirectory" && recorder
        ? recorder.beginDirectoryQuery() : null;
      const fallback = () => operation === "readFile" ? undefined
        : operation === "realpath" ? path.resolve(workspaceRoot)
          : ["getDirectories", "readDirectory"].includes(operation) ? [] : false;
      let result = fallback();
      if (access.allowed) {
        try {
          const probe = fileSystemObservation
            ? fileSystemObservation.run(() => original.call(host, candidate, ...args),
              ["getDirectories", "readDirectory"].includes(operation) ? accessFor : null, directoryTrace)
            : { value: original.call(host, candidate, ...args) };
          result = probe.value;
          if (probe.failure) {
            const expectedAbsence = access.status === "missing" && probe.failure.code === "ENOENT"
              && (["fileExists", "directoryExists"].includes(operation) && result === false
                || operation === "readFile" && result === undefined);
            access = { ...access, io_failure: { ...probe.failure, count: probe.count } };
            if (!expectedAbsence) {
              access = { ...access, status: "unavailable", error_code: probe.failure.code };
            }
          }
        } catch (error) {
          if (typeof error.code !== "string") throw error;
          access = { ...access, allowed: false, status: "unavailable", error_code: error.code };
        }
      }
      let filteredEntries = 0;
      if (Array.isArray(result)) {
        const entries = result.filter((item) => accessFor(
          path.isAbsolute(item) ? item : path.resolve(candidate, item),
        ).allowed);
        filteredEntries = result.length - entries.length;
        result = entries;
      }
      if (access.status === "missing" && (result === true || (operation === "readFile" && typeof result === "string"))) {
        access = { ...access, status: "changed_during_probe" };
      }
      if (access.status === "present" && operation === "readFile" && result === undefined) {
        // TypeScript's filesystem adapter can swallow I/O errors. An unknown
        // failed read of a present path is not proof that the file was absent.
        access = { ...access, status: "unavailable", error_code: "HOST_READ_UNAVAILABLE" };
      }
      if (accessState && (!access.allowed || filteredEntries
          || ["unavailable", "changed_during_probe"].includes(access.status))) {
        accessState.complete = false;
      }
      // Record the decision that guarded this call, not a second path check
      // after it: a later check could label different filesystem state.
      if (recorder) recorder.observe(stage, operation, candidate, args, access, result, filteredEntries,
        directoryTrace?.finish(!access.allowed || access.status !== "present" || !!filteredEntries));
      return result;
    };
  }
  return host;
}

function boundedParseHost(workspaceRoot, recorder, projectRoot, accessState) {
  return observeHost({ ...ts.sys, getCurrentDirectory: () => projectRoot }, "config", recorder,
    boundedPathAccess([{ root: workspaceRoot, scope: "workspace" }]), workspaceRoot, accessState);
}

function boundedCompilerHost(options, workspaceRoot, recorder, projectRoot) {
  const host = ts.createCompilerHost(options, true);
  // Each project owns resolution context; never change the process-wide CWD.
  host.getCurrentDirectory = () => projectRoot;
  const typescriptLibRoot = path.dirname(require.resolve("typescript"));
  // Mutate the host itself: TypeScript's getSourceFile closes over host.readFile.
  // A spread copy would miss the actual source reads in the observation manifest.
  return observeHost(host, "compiler", recorder, boundedPathAccess([
    { root: typescriptLibRoot, scope: "compiler_library" },
    { root: workspaceRoot, scope: "workspace" },
  ]), workspaceRoot);
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

function collectForProject(project, projectRoot, workspaceRoot, rootConfigFile, maxDiagnostics, maxFiles, semantic, contextLimit, directoryPolicy) {
  const tsconfig = path.join(projectRoot, rootConfigFile);
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

  const recorder = semantic ? semanticContextRecorder(contextLimit, directoryPolicy) : null;
  const parseAccess = { complete: true };
  const parseHost = boundedParseHost(workspaceRoot, recorder, projectRoot, parseAccess);
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

  const parsed = ts.parseJsonConfigFileContent(
    configFile.config, parseHost, projectRoot, undefined, tsconfig,
  );
  const requestedOptions = parsed.options;
  parsed.options = boundedCompilerOptions(parsed.options);
  const containedRootNames = parsed.fileNames.filter((fileName) => isResolvedPathWithin(workspaceRoot, fileName));
  const rootNames = maxFiles > 0 ? containedRootNames.slice(0, maxFiles) : containedRootNames;
  if (!semantic) {
    const diagnostics = [];
    const checkedFiles = [];
    for (const fileName of rootNames) {
      if (!isResolvedPathWithin(workspaceRoot, fileName)) continue;
      // Hash the exact text parsed, using the same UTF-8 decoding as Atlas.
      const content = fs.readFileSync(fileName, "utf8");
      const sourceFile = ts.createSourceFile(fileName, content, parsed.options.target || ts.ScriptTarget.Latest, true);
      checkedFiles.push(checkedSource(fileName, content, projectRoot));
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
      checked_source_manifest: {
        version: "v1",
        hash_basis: "sha256_utf8_source_text",
        scope: "checked_syntax_files_only",
        complete: parseAccess.complete && checkedFiles.length === parsed.fileNames.length
          && parsed.errors.length === 0,
        files: checkedFiles,
      },
      tsconfig: normalizePath(path.relative(projectRoot, tsconfig)),
      diagnostics_truncated: diagnostics.length > rows.length,
      diagnostics: rows,
      summary: {
        total: diagnostics.length,
        emitted: rows.length,
        by_code: byCode,
        by_category: byCategory,
        root_files_total: containedRootNames.length,
        root_files_checked: checkedFiles.length,
        root_enumeration_complete: parseAccess.complete,
      },
    };
  }

  const compilerHost = boundedCompilerHost(parsed.options, workspaceRoot, recorder, projectRoot);
  const program = ts.createProgram({
    rootNames,
    options: parsed.options,
    host: compilerHost,
  });
  const diagnostics = [
    ...parsed.errors,
    ...program.getOptionsDiagnostics(),
    ...program.getGlobalDiagnostics(),
    ...program.getSyntacticDiagnostics(),
    ...program.getSemanticDiagnostics(),
  ];
  const sourceFiles = program.getSourceFiles();
  const rows = diagnostics.slice(0, maxDiagnostics).map((diag) => diagnosticPayload(diag, projectRoot));
  // The bounded manifest must not spend its entire budget on library files
  // before recording emitted diagnostics or selected roots with no diagnostic.
  // Priority is only an output-coverage choice, never snapshot authority.
  const emittedSources = new Set(diagnostics.slice(0, maxDiagnostics)
    .map((diag) => diag.file?.fileName).filter(Boolean));
  const selectedRoots = new Set(rootNames);
  // Keep project-local Program dependencies ahead of compiler libraries when
  // the bounded manifest cannot hold every SourceFile. This is lexical scope
  // classification only; it does not add physical/link authority or I/O.
  const projectProgramSources = sourceFiles.filter((file) =>
    !file.isDeclarationFile && isPathWithin(projectRoot, file.fileName));
  const projectProgramNames = new Set(projectProgramSources.map((file) => file.fileName));
  const projectProgramDeclarations = sourceFiles.filter((file) =>
    file.isDeclarationFile && isPathWithin(projectRoot, file.fileName));
  const projectDeclarationNames = new Set(projectProgramDeclarations.map((file) => file.fileName));
  const workspaceExternalSources = sourceFiles.filter((file) =>
    isPathWithin(workspaceRoot, file.fileName) && !isPathWithin(projectRoot, file.fileName));
  const workspaceExternalNames = new Set(workspaceExternalSources.map((file) => file.fileName));
  const outsideWorkspaceSources = sourceFiles.filter((file) =>
    !isPathWithin(workspaceRoot, file.fileName));
  const prioritized = sourceFiles.filter((file) => emittedSources.has(file.fileName));
  const rootsWithoutDiagnostics = sourceFiles.filter((file) =>
    !emittedSources.has(file.fileName) && selectedRoots.has(file.fileName));
  const projectDependencies = projectProgramSources.filter((file) =>
    !emittedSources.has(file.fileName) && !selectedRoots.has(file.fileName));
  const projectDeclarations = projectProgramDeclarations.filter((file) =>
    !emittedSources.has(file.fileName) && !selectedRoots.has(file.fileName));
  const workspaceExternals = workspaceExternalSources.filter((file) =>
    !emittedSources.has(file.fileName) && !selectedRoots.has(file.fileName));
  const outsideWorkspace = outsideWorkspaceSources.filter((file) =>
    !emittedSources.has(file.fileName) && !selectedRoots.has(file.fileName));
  const orderedManifestFiles = [...prioritized, ...rootsWithoutDiagnostics,
    ...projectDependencies, ...projectDeclarations, ...workspaceExternals, ...outsideWorkspace];
  const manifestFiles = maxFiles > 0 ? orderedManifestFiles.slice(0, maxFiles) : orderedManifestFiles;
  const manifestProjectSources = manifestFiles.filter((file) => projectProgramNames.has(file.fileName));
  const manifestProjectDeclarations = manifestFiles.filter((file) =>
    projectDeclarationNames.has(file.fileName));
  const manifestWorkspaceExternals = manifestFiles.filter((file) =>
    workspaceExternalNames.has(file.fileName));
  const outsideWorkspaceNames = new Set(outsideWorkspaceSources.map((file) => file.fileName));
  const manifestOutsideWorkspace = manifestFiles.filter((file) =>
    outsideWorkspaceNames.has(file.fileName));
  const byCode = {};
  const byCategory = {};
  for (const row of diagnostics.map((diag) => diagnosticPayload(diag, projectRoot))) {
    byCode[row.code] = (byCode[row.code] || 0) + 1;
    byCategory[row.category] = (byCategory[row.category] || 0) + 1;
  }
  const blockers = ["auxiliary_inputs_not_atlas_bound", "resolution_observations_not_snapshot_authority",
    "bounded_compiler_profile_not_native_project_proof"];
  if (parsed.projectReferences?.length) blockers.push("project_references_not_loaded");
  if (!parseAccess.complete) blockers.push("config_host_access_incomplete");
  if (rootNames.length !== parsed.fileNames.length) blockers.push("root_inventory_incomplete");
  const context = recorder.finish({
    workspace_root: normalizePath(workspaceRoot), project_root: normalizePath(projectRoot),
    current_directory: normalizePath(compilerHost.getCurrentDirectory()),
    process_current_directory: normalizePath(ts.sys.getCurrentDirectory()),
    use_case_sensitive_file_names: ts.sys.useCaseSensitiveFileNames,
    compiler: observedCompilerIdentity(),
    requested_options_sha256: contextHash(requestedOptions),
    effective_options: parsed.options,
    overridden_options: [...new Set([...Object.keys(requestedOptions), ...Object.keys(parsed.options)])]
      .filter((key) => canonicalJson(requestedOptions[key]) !== canonicalJson(parsed.options[key])).sort(),
    configured_roots_sha256: contextHash(parsed.fileNames),
    selected_roots_sha256: contextHash(rootNames),
    configured_roots: parsed.fileNames.length,
    selected_roots: rootNames.length,
    project_references: (parsed.projectReferences || []).length,
    project_references_sha256: contextHash(parsed.projectReferences || []),
    binding_blockers: blockers,
  });
  return {
    project,
    project_root: normalizePath(projectRoot),
    status: "OK",
    mode: "semantic",
    semantic_context: context,
    checked_source_manifest: {
      version: "v1",
      hash_basis: "sha256_utf8_source_text",
      scope: "semantic_context_not_snapshot_bound",
      complete: false,
      files: manifestFiles.map((file) => checkedSource(file.fileName, file.text, projectRoot)),
      selected_root_files: rootNames.map((fileName) => normalizePath(path.relative(projectRoot, fileName))),
      program_project_source_files: manifestProjectSources.map((file) =>
        normalizePath(path.relative(projectRoot, file.fileName))),
      program_project_source_files_total: projectProgramSources.length,
      program_project_declaration_files: manifestProjectDeclarations.map((file) =>
        normalizePath(path.relative(projectRoot, file.fileName))),
      program_project_declaration_files_total: projectProgramDeclarations.length,
      program_workspace_external_files: manifestWorkspaceExternals.map((file) => ({
        file: normalizePath(path.resolve(file.fileName)), declaration: Boolean(file.isDeclarationFile),
      })),
      program_workspace_external_files_total: workspaceExternalSources.length,
      program_outside_workspace_files: manifestOutsideWorkspace.map((file) => ({
        file: normalizePath(path.resolve(file.fileName)), declaration: Boolean(file.isDeclarationFile),
      })),
      program_outside_workspace_files_total: outsideWorkspaceSources.length,
      source_files_total: sourceFiles.length,
      omitted_files: sourceFiles.length - manifestFiles.length,
    },
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
  // Both modes parse globs. The scoped enumeration guard is needed even when
  // semantic observations are disabled.
  fileSystemObservation = observeFileSystemFailures();
  ts = require("typescript");
  const diagnosticsPolicy = readJson(path.join(codeMapsDir, "config", "analysis_snapshot_lineage_contract.json"), null)
    ?.artifacts?.ts_diagnostics;
  const rootConfigFile = diagnosticsPolicy?.root_config_filename;
  if (typeof rootConfigFile !== "string" || !rootConfigFile
      || rootConfigFile !== path.basename(rootConfigFile)
      || /[/\\:]/.test(rootConfigFile)
      || !(rootConfigFile.toLowerCase().endsWith(".json")
        || rootConfigFile.toLowerCase().endsWith(".jsonc"))) {
    throw new Error("Invalid or unavailable TypeScript root configuration filename");
  }
  const contextPolicy = diagnosticsPolicy?.semantic_context;
  const directoryPolicy = diagnosticsPolicy?.atlas_resolution_inputs;
  const contextLimit = semantic ? Math.min(Number(args.maxContextEntries || contextPolicy?.max_observations),
    Number(contextPolicy?.max_observations)) : 0;
  if (semantic && (!Number.isSafeInteger(contextLimit) || contextLimit < 1)) {
    throw new Error("Invalid or unavailable semantic context observation budget");
  }
  if (semantic && ["max_entries", "max_name_bytes"].some(
    key => !Number.isSafeInteger(directoryPolicy?.[key]) || directoryPolicy[key] < 1,
  )) throw new Error("Invalid or unavailable directory input observation budget");
  const config = readJson(configPath, {});
  const workspaceRoot = path.resolve(codeMapsDir, config.workspace_root || "..");
  const variations = config.variations || {};
  const projects = {};
  const requestedProjects = String(args.projects || "")
    .split(",")
    .map((value) => value.trim().toUpperCase())
    .filter(Boolean);
  const allowedProjects = new Set(requestedProjects);
  if (semantic) observedCompilerIdentity();

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
        rootConfigFile,
        maxDiagnostics,
        maxFiles,
        semantic,
        contextLimit,
        directoryPolicy,
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
      collector_run_id: String(args.requestId || ""),
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

try {
  main();
} finally {
  if (fileSystemObservation) fileSystemObservation.restore();
}
