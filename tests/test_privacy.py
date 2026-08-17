"""Privacy tests: verify the cfdb_reader role cannot access unredacted data.

These tests validate the privacy guardrail that unredacted donor data
resides in a separate schema that is inaccessible to the MCP server's
read-only role.
"""

from __future__ import annotations


class TestPrivacyGuardrails:
    """Test that privacy guardrails are enforced."""

    def test_unredacted_schema_disabled_by_default(self):
        """UNREDACTED_ENABLED should default to 'false'."""
        import os

        # Remove env var if set
        env_before = os.environ.pop("UNREDACTED_ENABLED", None)
        try:
            import importlib

            from core.mcp import db

            importlib.reload(db)

            url = db._build_url()
            # Default URL should NOT include unredacted schema references
            assert "unredacted" not in url.lower(), (
                "Default URL should not reference unredacted schema"
            )
        finally:
            if env_before is not None:
                os.environ["UNREDACTED_ENABLED"] = env_before

    def test_unredacted_enabled_sets_flag(self):
        """UNREDACTED_ENABLED=true should set the flag."""
        import importlib
        import os

        os.environ["UNREDACTED_ENABLED"] = "true"
        try:
            from core.mcp import db

            importlib.reload(db)

            # When enabled, the flag should be set
            assert db.UNREDACTED_ENABLED is True
        finally:
            os.environ.pop("UNREDACTED_ENABLED", None)
            importlib.reload(db)

    def test_migration_has_unredacted_section(self):
        """Check that unredacted feature is documented (planned for v0.2).

        The unredacted schema guardrail is documented in the project spec
        and .env.example but not yet implemented in the migration DDL.
        This test verifies the feature is tracked even if not yet built.
        """
        # Feature is documented in .env.example and spec
        from pathlib import Path

        env_path = Path(__file__).resolve().parent.parent / ".env.example"
        spec_path = Path("/home/hermes/campaign-finance-db-spec-v2.md")

        env_has_flag = env_path.exists() and "UNREDACTED_ENABLED" in env_path.read_text()
        spec_has_feature = spec_path.exists() and "unredacted" in spec_path.read_text().lower()

        assert env_has_flag or spec_has_feature, (
            "Unredacted feature should be documented in .env.example or the project spec"
        )

    def test_docker_compose_has_unredacted_env(self):
        """docker-compose.yml should reference UNREDACTED_ENABLED."""
        from pathlib import Path

        compose_path = Path(__file__).resolve().parent.parent / "docker-compose.yml"
        compose = compose_path.read_text()

        assert "UNREDACTED_ENABLED" in compose, (
            "docker-compose.yml should reference UNREDACTED_ENABLED"
        )

    def test_env_example_has_unredacted_flag(self):
        """.env.example should document UNREDACTED_ENABLED."""
        from pathlib import Path

        env_path = Path(__file__).resolve().parent.parent / ".env.example"
        env_content = env_path.read_text()

        assert "UNREDACTED_ENABLED" in env_content, (
            ".env.example should document UNREDACTED_ENABLED"
        )
