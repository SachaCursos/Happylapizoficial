"""
Generate the interactive HTML dashboard using Jinja2.
Brand colors: #233dff (blue), #ffd51e (yellow). Font: Poppins.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


def _format_clp(value) -> str:
    try:
        v = float(value)
        return f"${v:,.0f}"
    except (TypeError, ValueError):
        return "$0"


def render_dashboard(context: dict) -> str:
    """Render dashboard.html with provided context dict."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    env.filters["clp"] = _format_clp
    template = env.get_template("dashboard.html")
    return template.render(**context)
