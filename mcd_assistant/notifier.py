"""Notification system for McDonald's Assistant."""
import json
import os
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


class Notifier:
    """Multi-channel notification system."""

    def __init__(self, method: str = "console", data_dir: str = ""):
        self.method = method
        self.data_dir = data_dir or "."
        self.log_file = os.path.join(self.data_dir, "notifications.jsonl")
        os.makedirs(self.data_dir, exist_ok=True)

    async def notify(
        self,
        title: str,
        message: str,
        level: str = "info",
        action: Optional[str] = None,
    ):
        """Send a notification through configured channels."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Console output with rich formatting
        color = {
            "info": "cyan",
            "success": "green",
            "warning": "yellow",
            "error": "red",
            "deal": "magenta",
        }.get(level, "white")

        icon = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
            "deal": "🎉",
        }.get(level, "📝")

        panel = Panel(
            f"{message}" + (f"\n\n[dim]已执行操作: {action}[/dim]" if action else ""),
            title=f"{icon} [{color}]{title}[/{color}]",
            border_style=color,
            padding=(1, 2),
        )
        console.print(panel)

        # Log to file
        entry = {
            "timestamp": timestamp,
            "title": title,
            "message": message,
            "level": level,
            "action": action,
        }
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def notify_deal(self, title: str, description: str, action: Optional[str] = None):
        """Notify about a new deal or promotion."""
        await self.notify(title, description, level="deal", action=action)

    async def notify_warning(self, title: str, description: str):
        """Send a warning notification."""
        await self.notify(title, description, level="warning")

    async def notify_error(self, title: str, description: str):
        """Send an error notification."""
        await self.notify(title, description, level="error")

    def print_table(self, title: str, headers: list, rows: list):
        """Print a formatted table."""
        table = Table(title=title, show_header=True, header_style="bold magenta")
        for h in headers:
            table.add_column(h)
        for row in rows:
            table.add_row(*[str(c) for c in row])
        console.print(table)

    def print_dashboard(self, sections: dict):
        """Print a multi-section dashboard."""
        for title, content in sections.items():
            panel = Panel(content, title=f"[bold]{title}[/bold]", border_style="blue")
            console.print(panel)
