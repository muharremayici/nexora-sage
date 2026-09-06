import os
import sys
import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from typing import Dict, Any

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.honesty_telemetry import record_honesty_event


def _max_trace_events() -> int:
    """Read bounded trace retention from the central runtime-output policy."""

    try:
        policy = load_json_file(CONFIG_DIR / "runtime_output_retention_policy.json", {})
        targets = policy.get("retention_targets", []) if isinstance(policy, dict) else []
        target = next(
            (row for row in targets if isinstance(row, dict) and row.get("id") == "telemetry_traces"),
            {},
        )
        return max(1, int(target.get("max_events")))
    except Exception as exc:
        record_honesty_event(
            component="local_telemetry_engine",
            category="storage_fallback",
            operation="load_trace_retention_policy",
            subject=str(CONFIG_DIR / "runtime_output_retention_policy.json"),
            reason="Local trace retention policy could not be loaded.",
            fallback="safe_local_trace_cap",
            claim_impact="runtime_timing_retention_requires_validation",
            exception=exc,
        )
        return 1000

TELEMETRY_PORT = 8890
TRACES_FILE = RAW_DIR / "telemetry_traces.json"
_server_instance = None
_server_thread = None

def init_traces_file():
    """Initializes the telemetry traces file if it does not exist."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if not TRACES_FILE.exists():
        save_json_atomic(
            TRACES_FILE,
            {
                "meta": {
                    "kind": "local_telemetry_traces",
                    "trace_origin_policy": "local traces are recorded only from explicit dev/test telemetry events",
                },
                "summary": {
                    "status": "PASS",
                    "status_reason": "No local runtime telemetry traces have been recorded.",
                    "trace_count": 0,
                    "hot_path_count": 0,
                },
                "traces": [],
                "hot_paths": {},
            },
        )

def load_traces() -> Dict[str, Any]:
    """Loads all captured local traces."""
    init_traces_file()
    dirty = False
    try:
        data = load_json_file(TRACES_FILE, {})
    except Exception as exc:
        record_honesty_event(
            component="local_telemetry_engine",
            category="caught_error",
            operation="load_traces",
            subject=str(TRACES_FILE),
            reason="runtime telemetry traces could not be loaded",
            fallback="empty trace set; no runtime claim permitted",
            claim_impact="runtime_evidence_unavailable",
            exception=exc,
        )
        data = {"traces": [], "hot_paths": {}}
    data.setdefault(
        "meta",
        {
            "kind": "local_telemetry_traces",
            "trace_origin_policy": "local traces are recorded only from explicit dev/test telemetry events",
        },
    )
    if "traces" not in data:
        data["traces"] = []
        dirty = True
    if "hot_paths" not in data:
        data["hot_paths"] = {}
        dirty = True
    summary = _trace_summary(data)
    if data.get("summary") != summary:
        data["summary"] = summary
        dirty = True
    for trace in data.get("traces", []):
        if isinstance(trace, dict):
            if "trace_origin" not in trace:
                trace["trace_origin"] = "legacy"
                dirty = True
    if dirty:
        save_json_atomic(TRACES_FILE, data)
    return data


def _trace_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    traces = data.get("traces", []) if isinstance(data, dict) else []
    hot_paths = data.get("hot_paths", {}) if isinstance(data, dict) else {}
    trace_count = len(traces) if isinstance(traces, list) else 0
    hot_path_count = len(hot_paths) if isinstance(hot_paths, dict) else 0
    status = "ATTENTION" if trace_count else "PASS"
    reason = (
        f"Local runtime telemetry contains {trace_count} traces across {hot_path_count} hot paths."
        if trace_count
        else "No local runtime telemetry traces have been recorded."
    )
    return {
        "status": status,
        "status_reason": reason,
        "trace_count": trace_count,
        "hot_path_count": hot_path_count,
    }

def record_trace(trace_type: str, identifier: str, execution_ms: int = 0, trace_origin: str = "local") -> Dict[str, Any]:
    """Atomically appends a telemetry trace and updates aggregated hot-path weights."""
    data = load_traces()
    
    # 1. Append trace record
    new_trace = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type": trace_type,
        "identifier": identifier,
        "execution_ms": execution_ms,
        "trace_origin": trace_origin or "local",
    }
    data["traces"].append(new_trace)
    
    # Retention is central policy; this runtime ledger is advisory, not truth.
    max_events = _max_trace_events()
    if len(data["traces"]) > max_events:
        data["traces"] = data["traces"][-max_events:]
        
    # 2. Update hot paths
    hot_paths = data.get("hot_paths", {})
    hot_paths[identifier] = hot_paths.get(identifier, 0) + 1
    data["hot_paths"] = hot_paths
    data["summary"] = _trace_summary(data)
    
    save_json_atomic(TRACES_FILE, data)
    return new_trace

class TelemetryHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default request logging to keep dev console clean
        pass

    def do_POST(self):
        if self.path == "/telemetry/trace":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")
                payload = json.loads(body)
                
                trace_type = payload.get("type", "unknown")
                identifier = payload.get("identifier", "unknown")
                execution_ms = int(payload.get("execution_ms", 0))
                trace_origin = payload.get("trace_origin", "local")
                
                new_trace = record_trace(trace_type, identifier, execution_ms, trace_origin=trace_origin)
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "OK", "trace": new_trace}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Bad Request: {str(e)}".encode("utf-8"))
                
        elif self.path == "/telemetry/stop":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Stopping Telemetry Server...")
            
            # Shutdown server in a separate thread
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/telemetry/hotpaths":
            data = load_traces()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data.get("hot_paths", {}), indent=2).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

def start_telemetry_server():
    """Starts the local telemetry HTTP server in a background thread."""
    global _server_instance, _server_thread
    init_traces_file()
    
    if _server_instance is not None:
        logger.info(f"Local Telemetry Server is already running on port {TELEMETRY_PORT}.")
        return True
        
    try:
        _server_instance = HTTPServer(("127.0.0.1", TELEMETRY_PORT), TelemetryHandler)
        _server_thread = threading.Thread(target=_server_instance.serve_forever, daemon=True)
        _server_thread.start()
        logger.info(f"Local Telemetry Server launched successfully on 127.0.0.1:{TELEMETRY_PORT} ✅")
        return True
    except Exception as e:
        logger.error(f"Failed to start local telemetry server: {e}")
        return False

def stop_telemetry_server():
    """Stops the active local telemetry server."""
    global _server_instance, _server_thread
    if _server_instance:
        logger.info("Stopping Local Telemetry Server...")
        _server_instance.shutdown()
        _server_instance.server_close()
        _server_instance = None
        _server_thread = None
        logger.info("Local Telemetry Server stopped successfully.")
        return True
    return False

def get_hybrid_telemetry_analysis() -> Dict[str, Any]:
    """
    Correlates runtime dynamic telemetry traces with the static semantic atlas nodes.
    Identifies dynamic execution hot-spots and execution gaps.
    """
    traces_data = load_traces()
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    
    hot_paths = traces_data.get("hot_paths", {})
    traces = traces_data.get("traces", [])
    trace_origin_summary = {}
    for trace in traces:
        origin = trace.get("trace_origin", "legacy") if isinstance(trace, dict) else "legacy"
        trace_origin_summary[origin] = trace_origin_summary.get(origin, 0) + 1

    trace_projects = {
        str(trace.get("identifier") or "").split("::", 1)[0]
        for trace in traces
        if isinstance(trace, dict) and "::" in str(trace.get("identifier") or "")
    }
    analysis_projects = set(trace_projects)
    if "MAIN" in (atlas or {}):
        analysis_projects.add("MAIN")
    
    # Calculate execution times averages per component/route
    exec_times = {}
    for trace in traces:
        ident = trace.get("identifier")
        ms = trace.get("execution_ms", 0)
        if ident:
            if ident not in exec_times:
                exec_times[ident] = []
            exec_times[ident].append(ms)
            
    avg_exec_times = {}
    for ident, times in exec_times.items():
        avg_exec_times[ident] = round(sum(times) / len(times), 2)
        
    # Collate static nodes from atlas, preferring agent-operational workspace paths.
    all_static_nodes = []
    node_aliases: dict[str, str] = {}
    if atlas:
        for pkey, pdata in atlas.items():
            if pkey == "symbols":
                continue
            if analysis_projects and pkey not in analysis_projects:
                continue
            for f_rel, meta in pdata.get("files", {}).items():
                atlas_rel = str(f_rel).replace("\\", "/")
                workspace_rel = ""
                if isinstance(meta, dict):
                    workspace_rel = str(meta.get("workspace_rel") or "").replace("\\", "/").strip()
                display_rel = workspace_rel or atlas_rel
                display_node = f"{pkey}::{display_rel}"
                all_static_nodes.append(display_node)
                node_aliases[f"{pkey}::{atlas_rel}"] = display_node
                node_aliases[display_node] = display_node
                
    mapped_hotpaths = []
    untriggered_nodes = []
    
    # Trace mapping and bridge rules
    for ident, hit_count in sorted(hot_paths.items(), key=lambda x: x[1], reverse=True):
        # Resolve target static node
        target_node = "UNKNOWN"
        for alias, node in node_aliases.items():
            # Direct match
            if ident == alias or ident == node or ident in alias or ident in node:
                target_node = node
                break
                
        mapped_hotpaths.append({
            "identifier": ident,
            "mapped_node": target_node,
            "hits": hit_count,
            "avg_ms": avg_exec_times.get(ident, 0.0)
        })
        
    # Identify gaps (nodes in static atlas never hit in local telemetry)
    hit_nodes = {item["mapped_node"] for item in mapped_hotpaths if item["mapped_node"] != "UNKNOWN"}
    for node in all_static_nodes:
        if node not in hit_nodes:
            untriggered_nodes.append(node)
            
    return {
        "mapped_hotpaths": mapped_hotpaths,
        "untriggered_nodes_count": len(untriggered_nodes),
        "untriggered_nodes_sample": untriggered_nodes[:15],
        "total_captured_traces": len(traces),
        "trace_origin_summary": trace_origin_summary,
        "analysis_projects": sorted(analysis_projects),
        "coverage_gap_label": "local_session_coverage_gap",
    }

def run_local_telemetry_report():
    """Orchestrator runner entry point. Prints the dynamic execution dashboard."""
    logger.info("Aggregating Dynamic Dev Telemetry Traces...")
    
    traces_data = load_traces()
    if not traces_data.get("traces"):
        logger.info("[!] No local traces found. Writing empty telemetry analysis; no demo trace is counted as runtime evidence.")
        
    analysis = get_hybrid_telemetry_analysis()
    
    # Save report
    output_path = REPORTS_DIR / "local_telemetry_analysis.json"
    save_json_atomic(output_path, analysis)
    
    # Beautiful visual dashboard print
    print("\n" + "=" * 65)
    print("=== LOCAL DEV TELEMETRY & RUNTIME HOT-PATH DASHBOARD ===")
    print("=" * 65)
    
    print(f"{'IDENTIFIER / ROUTE':<35} | {'HITS':<6} | {'AVG TIME':<10} | {'MAPPED STATIC NODE'}")
    print("-" * 65)
    for path in analysis["mapped_hotpaths"][:10]:
        mapped = path["mapped_node"]
        if mapped != "UNKNOWN":
            mapped = mapped.split("::")[-1] # display basename
        print(f"{path['identifier']:<35} | {path['hits']:<6} | {path['avg_ms']:.1f} ms   | {mapped}")
    print("-" * 65)
    
    print(f"\n[+] Total Telemetry Traces Captured: {analysis['total_captured_traces']}")
    print(f"[+] Static Nodes without Local Session Hits (Coverage Gaps): {analysis['untriggered_nodes_count']} components")
    
    if analysis["untriggered_nodes_sample"]:
        print("\n[!] Local Session Coverage Gap Sample:")
        for node in analysis["untriggered_nodes_sample"][:5]:
            print(f"  - {node}")
            
    print("=" * 65 + "\n")
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true", help="Start the local telemetry listener HTTP server")
    parser.add_argument("--stop", action="store_true", help="Stop the active local telemetry listener server")
    args = parser.parse_args()
    
    if args.start:
        start_telemetry_server()
        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_telemetry_server()
    elif args.stop:
        # Send HTTP POST /telemetry/stop to stop
        import urllib.request
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{TELEMETRY_PORT}/telemetry/stop", method="POST")
            urllib.request.urlopen(req)
            print("Stop request sent successfully.")
        except Exception as e:
            print(f"Could not stop server: {e}")
    else:
        success = run_local_telemetry_report()
        sys.exit(0 if success else 1)
