"""Static checks for security-sensitive frontend template patterns."""

from __future__ import annotations

from pathlib import Path


def test_analytics_template_avoids_innerhtml_in_dynamic_tables() -> None:
    """Dynamic analytics rendering should use textContent, not innerHTML."""
    template_path = Path("hub/templates/analytics.html")
    content = template_path.read_text(encoding="utf-8")

    assert "innerHTML =" not in content
    assert "textContent" in content
