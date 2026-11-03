"""Doc-drift guard: every registered MCP tool must be documented.

The `get_server_docs` tool ships docs/mcp_server.md to connected agents, and
docs/MCP_TOOLS.md is the query-intent cheat sheet. Both go stale silently
when a tool is added/renamed. This test fails if:

  1. the live server's registered tools != the canonical TOOLS list, or
  2. any registered tool is missing from docs/mcp_server.md, or
  3. any registered tool is missing from docs/MCP_TOOLS.md.

Names are matched backtick-wrapped (`tool_name`) as they appear in the docs.
"""

from pathlib import Path
import re

from core.mcp.server import TOOLS, _create_server

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER_DOC = REPO_ROOT / "docs" / "mcp_server.md"
CHEAT_SHEET_DOC = REPO_ROOT / "docs" / "MCP_TOOLS.md"


def _registered_tool_names() -> list[str]:
    return sorted(t.name for t in _create_server()._tool_manager.list_tools())


def _documented(text: str, tool: str) -> bool:
    """True if the tool name appears as a whole word.

    Matches both the bare `` `tool` `` form (mcp_server.md catalog) and the
    `` `tool(arg, …)` `` call-signature form (cheat sheet), via word
    boundaries — a name embedded in a longer identifier won't match.
    """
    return re.search(rf"\b{re.escape(tool)}\b", text) is not None


def test_registered_tools_match_canonical_list():
    """A tool added to the server but not TOOLS (or vice-versa) is a drift bug."""
    registered = _registered_tool_names()
    assert registered == sorted(TOOLS), (
        "server registration and TOOLS list diverge; "
        f"only-registered={set(registered) - set(TOOLS)}, "
        f"only-in-TOOLS={set(TOOLS) - set(registered)}"
    )


def test_every_tool_documented_in_mcp_server_md():
    text = MCP_SERVER_DOC.read_text(encoding="utf-8")
    missing = [t for t in TOOLS if not _documented(text, t)]
    assert not missing, f"tools missing from docs/mcp_server.md: {missing}"


def test_every_tool_documented_in_cheat_sheet():
    text = CHEAT_SHEET_DOC.read_text(encoding="utf-8")
    missing = [t for t in TOOLS if not _documented(text, t)]
    assert not missing, f"tools missing from docs/MCP_TOOLS.md: {missing}"


def test_cheat_sheet_exists_and_nontrivial():
    assert CHEAT_SHEET_DOC.exists(), "docs/MCP_TOOLS.md cheat sheet is required"
    assert len(CHEAT_SHEET_DOC.read_text(encoding="utf-8")) > 1500, (
        "cheat sheet looks too short to cover the tool surface"
    )
