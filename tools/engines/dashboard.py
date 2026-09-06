import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.config import RAW_DIR, REPORTS_DIR, CODE_MAPS_DIR as BASE_DIR
from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.live import Live
    from rich.layout import Layout
    from rich.columns import Columns
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = Console()

def load_json(path: Path):
    try:
        return load_json_file(path, None)
    except Exception:
        return None

def make_layout() -> Layout:
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split(
        Layout(name="stats", size=10),
        Layout(name="violations"),
    )
    return layout

class NexoraDashboard:
    def __init__(self):
        self.last_update = datetime.now()
        
    def get_stats_panel(self, atlas, audit, health):
        table = Table.grid(expand=True)
        table.add_column(style="cyan", justify="right")
        table.add_column(style="white", justify="left")
        
        project_count = len(atlas) if atlas else 0
        file_count = sum(len(p.get("files", {})) for p in atlas.values()) if atlas else 0
        violation_count = len(audit.get("violations", [])) if audit else 0
        score = health.get("score", "N/A") if health else "N/A"
        grade = health.get("grade", "N/A") if health else "N/A"
        
        table.add_row("Projects:", str(project_count))
        table.add_row("Total Files:", str(file_count))
        table.add_row("Violations:", f"[bold red]{violation_count}[/bold red]" if violation_count > 0 else "0")
        table.add_row("Health Score:", f"[bold green]{score}[/bold green]" if isinstance(score, (int, float)) and score > 80 else str(score))
        table.add_row("Grade:", f"[bold blue]{grade}[/bold blue]")
        
        return Panel(table, title="[bold]Workspace Stats[/bold]", border_style="bright_blue")

    def get_violation_summary(self, audit):
        table = Table(title="Top Violations by Rule", box=box.SIMPLE, expand=True)
        table.add_column("Rule", style="red")
        table.add_column("Count", style="white", justify="right")
        
        if not audit:
            return Panel("No audit data.", title="Violations")
            
        by_rule = {}
        for v in audit.get("violations", []):
            rule = v.get("rule", "unknown")
            by_rule[rule] = by_rule.get(rule, 0) + 1
            
        sorted_rules = sorted(by_rule.items(), key=lambda x: x[1], reverse=True)
        for rule, count in sorted_rules[:8]:
            table.add_row(rule, str(count))
            
        return table

    def get_critical_nodes(self, blast):
        table = Table(title="Critical Impact Nodes", box=box.SIMPLE, expand=True)
        table.add_column("Node", style="yellow")
        table.add_column("Impact", style="magenta", justify="right")
        
        if not blast:
            return Panel("No blast radius data.", title="Criticality")
            
        nodes = []
        for project_data in blast.get("by_project", {}).values():
            for node, score in project_data.get("impact_scores", {}).items():
                nodes.append((node, score))
                
        sorted_nodes = sorted(nodes, key=lambda x: x[1], reverse=True)
        for node, score in sorted_nodes[:12]:
            short_node = node.split("::")[-1]
            table.add_row(short_node, f"{score:.1f}")
            
        return table

    def generate_content(self) -> Layout:
        atlas = load_atlas_data() or {}
        audit = load_json_file(RAW_DIR / "audit_report.json", {}) or {}
        health = load_json_file(RAW_DIR / "health_score.json", {}) or {}
        blast = load_json_file(RAW_DIR / "blast_radius.json", {}) or {}
        
        layout = make_layout()
        
        layout["header"].update(
            Panel(
                f"[bold cyan]Nexora S.A.G.E. Interactive Dashboard[/bold cyan] | [dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
                border_style="bright_blue"
            )
        )
        
        layout["stats"].update(self.get_stats_panel(atlas, audit, health))
        layout["violations"].update(self.get_violation_summary(audit))
        layout["right"].update(Panel(self.get_critical_nodes(blast), title="[bold]Impact Analysis[/bold]", border_style="magenta"))
        
        layout["footer"].update(
            Panel(
                "[dim]Press Ctrl+C to exit. Dashboard auto-refreshes when pipeline artifacts update.[/dim]",
                border_style="dim"
            )
        )
        
        return layout

def run_dashboard():
    if not RICH_AVAILABLE:
        print("Rich library not found. Please install it with 'pip install rich'.")
        return

    dash = NexoraDashboard()
    
    with Live(dash.generate_content(), refresh_per_second=1, screen=True) as live:
        try:
            while True:
                live.update(dash.generate_content())
                time.sleep(2)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    run_dashboard()
