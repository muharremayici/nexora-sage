import json
import os
import sys
from pathlib import Path

# Sovereign Root Recovery (v19.7)
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.core.config import DOCTRINE_FILE, save_json_atomic

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = Console()

def load_doctrine():
    if not DOCTRINE_FILE.exists():
        return {}
    with open(DOCTRINE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def list_mappings(doctrine):
    studio_defs = doctrine.get("studio_definitions", {})
    table = Table(title="Nexora Studio Mapping Registry", box=box.ROUNDED)
    table.add_column("Studio ID", style="cyan")
    table.add_column("Keywords / Path Fragments", style="yellow")
    
    for studio, keywords in studio_defs.items():
        table.add_row(studio, ", ".join(keywords))
    
    console.print(table)

def add_mapping(doctrine):
    studio = Prompt.ask("Enter Studio ID (e.g., presentation, domain)")
    keywords_raw = Prompt.ask("Enter keywords (comma-separated)")
    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    
    studio_defs = doctrine.get("studio_definitions", {})
    if studio in studio_defs:
        if not Confirm.ask(f"Studio '{studio}' already exists. Append keywords?"):
            return
        studio_defs[studio].extend([k for k in keywords if k not in studio_defs[studio]])
    else:
        studio_defs[studio] = keywords
        
    doctrine["studio_definitions"] = studio_defs
    save_json_atomic(DOCTRINE_FILE, doctrine)
    console.print(f"[bold green]✓[/bold green] Studio mapping for '{studio}' updated.")

def run_mapper():
    if not RICH_AVAILABLE:
        print("Rich library required.")
        return
        
    doctrine = load_doctrine()
    if not doctrine:
        console.print("[bold red]Error:[/bold red] Architecture doctrine not found.")
        return
        
    while True:
        console.clear()
        console.print(Panel("[bold cyan]Nexora Studio Mapper[/bold cyan]\nInteractive Discovery Configuration", border_style="bright_blue"))
        list_mappings(doctrine)
        
        choice = Prompt.ask(
            "\n[bold]Actions[/bold]",
            choices=["add", "list", "exit"],
            default="list"
        )
        
        if choice == "add":
            add_mapping(doctrine)
            # Reload for consistency
            doctrine = load_doctrine()
        elif choice == "list":
            time.sleep(0.5) # Just refresh
        elif choice == "exit":
            break

if __name__ == "__main__":
    run_mapper()
