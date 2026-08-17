"""Background entity resolution workflow.

Runs nightly to scan for potential duplicates across the entity table.
Creates merge suggestions in the entity_merge_queue for human review.

Usage:
    python -m core.workflows.entity_resolution --dry-run
    python -m core.workflows.entity_resolution --apply
"""

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.etl.entity_resolution import EntityResolver
from core.etl.logging import setup_logging

logger = logging.getLogger(__name__)


def scan_for_duplicates(
    resolver: EntityResolver,
    batch_size: int = 1000,
) -> list[tuple[int, int, float, str]]:
    """Scan entity table for potential duplicates using trigram similarity.

    Queries all entities whose name similarity exceeds a threshold,
    then creates merge suggestions for high-confidence matches.

    Args:
        resolver: EntityResolver instance.
        batch_size: Number of entities to scan per query batch.

    Returns:
        List of (source_id, target_id, score, method) tuples for all
        merge suggestions created.
    """
    suggestions: list[tuple[int, int, float, str]] = []

    # Find all entities with trigram similarity above threshold
    query = text(
        """
        SELECT
            e1.entity_id,
            e2.entity_id,
            round(
                similarity(lower(e1.naml), lower(e2.naml)),
                4
            ) AS sim_score
        FROM entity e1
        JOIN entity e2
            ON e1.entity_id < e2.entity_id
            AND similarity(lower(e1.naml), lower(e2.naml)) >= :threshold
            AND levenshtein(lower(e1.naml), lower(e2.naml)) <= :lev_limit
        ORDER BY sim_score DESC
        LIMIT :limit
        """
    )

    # Get count first to batch properly
    count_query = text(
        "SELECT COUNT(*) FROM entity WHERE naml IS NOT NULL AND naml != ''"
    )

    with resolver._conn.begin() as conn:
        row = conn.execute(count_query).fetchone()
        total = row[0] if row else 0
        logger.info("Scanning %d entities for duplicates", total)

        # Scan in batches
        offset = 0
        while offset < total:
            batch = conn.execute(query, {
                "threshold": resolver.similarity_threshold,
                "lev_limit": resolver.distance_threshold,
                "limit": batch_size,
            }).fetchall()

            if not batch:
                break

            for source_id, target_id, sim_score in batch:
                # Skip if either is already resolved
                if _is_resolved(conn, source_id) or _is_resolved(conn, target_id):
                    continue

                # Skip if already in queue
                if _already_queued(conn, source_id, target_id):
                    continue

                resolver.suggest_merge(
                    entity_a_id=source_id,
                    entity_b_id=target_id,
                    match_score=sim_score,
                    match_method="batch_scan",
                    notes=f"Automated scan: trigram similarity {sim_score:.4f}",
                )
                suggestions.append((source_id, target_id, sim_score, "batch_scan"))

            offset += batch_size
            logger.info("Scanned %d/%d entities", offset, total)

    logger.info("Created %d merge suggestions", len(suggestions))
    return suggestions


def _is_resolved(conn, entity_id: int) -> bool:
    """Check if entity has already been resolved into another."""
    row = conn.execute(
        text("SELECT resolved_by FROM entity WHERE entity_id = :eid"),
        {"eid": entity_id},
    ).fetchone()
    return row is not None and row[0] is not None


def _already_queued(conn, entity_a_id: int, entity_b_id: int) -> bool:
    """Check if a merge between these two entities is already pending."""
    row = conn.execute(
        text(
            "SELECT 1 FROM entity_merge_queue "
            "WHERE status = 'pending' "
            "AND entity_a_id = :a AND entity_b_id = :b"
        ),
        {"a": entity_a_id, "b": entity_b_id},
    ).fetchone()
    return row is not None


def run_entity_resolution(
    engine,
    dry_run: bool = False,
    apply: bool = False,
) -> dict:
    """Run the entity resolution workflow.

    Args:
        engine: SQLAlchemy Engine.
        dry_run: If True, only scan and report without creating suggestions.
        apply: If True, automatically apply high-confidence merges.

    Returns:
        Dict with scan results and actions taken.
    """
    resolver = EntityResolver(engine=engine)

    results = {
        "scanned": 0,
        "suggestions_created": 0,
        "merges_applied": 0,
        "merges_rejected": 0,
        "errors": [],
    }

    try:
        # Scan for duplicates
        suggestions = scan_for_duplicates(resolver)
        results["scanned"] = len(suggestions)
        results["suggestions_created"] = len(suggestions)

        if dry_run:
            logger.info("[DRY RUN] Created %d suggestions (not persisted)", len(suggestions))
            return results

        if apply:
            # Automatically apply high-confidence merges (> 0.95 score)
            for source_id, target_id, score, method in suggestions:
                if score >= 0.95:
                    try:
                        # Find the queue_id for this pair
                        with resolver._conn.begin() as conn:
                            row = conn.execute(
                                text(
                                    "SELECT queue_id FROM entity_merge_queue "
                                    "WHERE entity_a_id = :a AND entity_b_id = :b "
                                    "AND status = 'pending'"
                                ),
                                {"a": source_id, "b": target_id},
                            ).fetchone()

                            if row:
                                resolver.apply_merge(
                                    queue_id=row[0],
                                    reviewed_by="auto_apply",
                                )
                                results["merges_applied"] += 1
                    except Exception as e:
                        results["errors"].append(str(e))

    except Exception as e:
        results["errors"].append(f"Scan failed: {e}")
        logger.error("Entity resolution scan failed: %s", e)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Entity resolution workflow")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan without creating suggestions"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Auto-apply high-confidence merges"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()
    level = "DEBUG" if args.verbose else "INFO"
    setup_logging(level=level)

    from config.settings import get_database_url
    engine = create_engine(get_database_url())

    results = run_entity_resolution(engine, dry_run=args.dry_run, apply=args.apply)

    logger.info("Entity resolution complete:")
    logger.info("  Scanned: %d", results["scanned"])
    logger.info("  Suggestions: %d", results["suggestions_created"])
    logger.info("  Merged: %d", results["merges_applied"])
    if results["errors"]:
        logger.warning("Errors: %s", results["errors"])


if __name__ == "__main__":
    main()
