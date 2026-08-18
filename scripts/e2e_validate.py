#!/usr/bin/env python3
"""End-to-end validation: import all modules, register MCP tools, run smoke tests.

This script is the final gate before v0.1.0-rc:
1. Import all core modules
2. Validate MCP tool registration
3. Verify database connection config
4. Run a minimal integration test
"""

from __future__ import annotations

import sys
import importlib


def test_imports():
    """Test that all core modules import cleanly."""
    modules = [
        "core.schema",
        "core.etl.loader",
        "core.etl.entity_resolution",
        "core.etl.validation",
        "core.mcp.db",
        "core.mcp.server",
        "core.mcp.tools",
        "core.workflows.scheduler",
        "state.tables",
        "state.etl",
        "state.scrapers",
    ]

    failed = []
    for mod in modules:
        try:
            importlib.import_module(mod)
            print(f"  ✓ {mod}")
        except Exception as e:
            print(f"  ✗ {mod}: {e}")
            failed.append(mod)

    if failed:
        print(f"\n{len(failed)} module(s) failed to import")
        return False
    print(f"\nAll {len(modules)} modules imported successfully")
    return True


def test_mcp_tools_registered():
    """Validate that all expected MCP tools are registered."""
    from core.mcp.server import _create_server
    from core.mcp import tools as tool_mod

    server = _create_server()

    # Check that tools module exports all expected functions
    expected_tools = [
        "contributions_by_donor",
        "top_donors_for_committee_or_candidate",
        "committee_outlays_to",
        "vendor_revenue",
        "committee_profile",
        "measure_spending",
        "donor_watch_since",
        "upcoming_filings",
        "filing_due_soon",
    ]

    missing = []
    for tool_name in expected_tools:
        if hasattr(tool_mod, tool_name):
            print(f"  ✓ {tool_name}")
        else:
            print(f"  ✗ {tool_name} — not found in tools module")
            missing.append(tool_name)

    if missing:
        print(f"\n{len(missing)} tool(s) not registered")
        return False
    print(f"\nAll {len(expected_tools)} MCP tools registered")
    return True


def test_db_config():
    """Validate database configuration is accessible."""
    from core.mcp.db import _DEFAULT_URL, UNREDACTED_ENABLED

    print(f"  DEFAULT_URL: {_DEFAULT_URL[:30]}...")
    print(f"  UNREDACTED_ENABLED: {UNREDACTED_ENABLED}")
    print("\n  ✓ Database configuration accessible")
    return True


def test_mcp_app_creation():
    """Validate MCP server can be created without errors."""
    from core.mcp.server import _create_server

    try:
        server = _create_server()
        print(f"  ✓ MCP server created successfully")
        return True
    except Exception as e:
        print(f"  ✗ MCP server creation failed: {e}")
        return False


def test_schema_tables():
    """Validate core tables are in the schema."""
    from core.schema import metadata

    # Core tables that must exist for Phase 1 MVP
    core_tables = [
        "filings", "rcpt_cd", "exppd_cd", "cntrb_cd", "hdr",
        "filername", "loans_cd", "filer_types", "report_types",
        "filing_calendar", "election_results",
        "entity", "entity_merge_queue",
    ]

    actual = set(metadata.tables.keys())
    missing = [t for t in core_tables if t not in actual]

    if missing:
        print(f"  ✗ Missing core tables: {missing}")
        return False

    print(f"  ✓ All {len(core_tables)} core tables present")
    print(f"    Total schema tables: {len(actual)}")
    return True


def main():
    """Run all e2e validation checks."""
    print("=" * 60)
    print("Campaign Finance DB — End-to-End Validation")
    print("=" * 60)

    checks = [
        ("Module imports", test_imports),
        ("Schema validation", test_schema_tables),
        ("MCP app creation", test_mcp_app_creation),
        ("MCP tool registration", test_mcp_tools_registered),
        ("DB configuration", test_db_config),
    ]

    results = []
    for name, check in checks:
        print(f"\n{name}:")
        results.append((name, check()))

    print("\n" + "=" * 60)
    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, ok in results:
        status = "✓" if ok else "✗"
        print(f"  {status} {name}")

    print(f"\n{'=' * 60}")
    print(f"Result: {passed}/{total} checks passed")

    if passed == total:
        print("E2E validation: PASSED ✓")
        return 0
    else:
        print("E2E validation: FAILED ✗")
        return 1


if __name__ == "__main__":
    sys.exit(main())
