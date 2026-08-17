"""Entity resolution: fuzzy matching for donor/vendor disambiguation.

Uses pg_trgm similarity + fuzzystrmatch distance for fuzzy matching,
with a merge queue for human review before applying merges.

The `pg_trgm` and `fuzzystrmatch` extensions must be enabled in the
database before this module can function:

    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine, Connection

logger = logging.getLogger(__name__)


@dataclass
class EntityMatch:
    """A candidate match from the entity resolution engine."""

    source_id: int
    source_naml: str
    source_namf: str
    source_nams: str
    source_city: str
    source_st: str
    target_id: int
    target_naml: str
    target_namf: str
    target_nams: str
    target_city: str
    target_st: str
    similarity: float
    match_method: str
    match_score: float


@dataclass
class MergeSuggestion:
    """A pending merge in the entity_merge_queue."""

    queue_id: int | None = None
    entity_a_id: int | None = None
    entity_b_id: int | None = None
    match_score: float = 0.0
    match_method: str = ""
    status: str = "pending"
    reviewed_by: str | None = None
    reviewed_at: Any | None = None
    notes: str | None = None


class EntityResolver:
    """Fuzzy entity matching and merge management.

    Args:
        engine: SQLAlchemy Engine or Connection.
        similarity_threshold: Trigram similarity cutoff (default 0.7).
        distance_threshold: Levenshtein distance cutoff for secondary matching.
    """

    def __init__(
        self,
        engine: Engine,
        similarity_threshold: float = 0.7,
        distance_threshold: int = 3,
    ) -> None:
        self._conn = engine
        self.similarity_threshold = similarity_threshold
        self.distance_threshold = distance_threshold

    def _ensure_extensions(self, conn: Connection) -> None:
        """Ensure pg_trgm and fuzzystrmatch extensions exist (Postgres only).
        
        On SQLite, these extensions are not available but also not needed
        for unit tests that only exercise merge queue operations.
        """
        try:
            dialect_name = conn.engine.dialect.name
        except Exception:
            dialect_name = ""
        if "postgresql" in dialect_name:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch"))

    def find_matches(
        self,
        name: str,
        city: str | None = None,
        state: str | None = None,
        limit: int = 10,
    ) -> list[EntityMatch]:
        """Find candidate entity matches for a given name.

        Uses trigram similarity (primary) and Levenshtein distance
        (secondary) to rank candidates.

        Args:
            name: The name to match against.
            city: Optional city filter.
            state: Optional state filter.
            limit: Maximum number of results.

        Returns:
            List of EntityMatch objects sorted by match_score descending.
        """
        name_lower = name.lower()
        name_normalized = "".join(ch for ch in name_lower if ch.isalnum() or ch == " ")

        with self._conn.begin() as conn:
            self._ensure_extensions(conn)

            # Build query with optional city/state filters
            city_filter = ""
            city_params: dict[str, Any] = {"city": city}
            if city:
                city_filter = "AND e.naml LIKE :city_mask OR e.namf LIKE :city_mask"
                city_params["city_mask"] = f"%{city.lower()}%"

            state_filter = ""
            state_params: dict[str, Any] = {"state": state}
            if state:
                state_filter = "AND e.namf = :state"
                state_params["state"] = state[:2].upper()

            query = text(
                f"""
                SELECT
                    e1.entity_id AS source_id,
                    e1.naml AS source_naml,
                    e1.namf AS source_namf,
                    e1.nams AS source_nams,
                    e2.entity_id AS target_id,
                    e2.naml AS target_naml,
                    e2.namf AS target_namf,
                    e2.nams AS target_nams,
                    e2.city AS target_city,
                    e2.st AS target_st,
                    round(
                        similarity(lower(e1.naml), lower(e2.naml))
                        + case when lower(e1.namf) = lower(e2.namf) then 0.2 else 0 end
                        + case when lower(e1.nams) = lower(e2.nams) then 0.1 else 0 end
                        + case when lower(e1.city) = lower(e2.city) then 0.1 else 0 end,
                        4
                    ) AS match_score
                FROM entity e1
                JOIN entity e2
                    ON e1.entity_id != e2.entity_id
                    AND similarity(lower(e1.naml), lower(e2.naml)) > :sim_threshold
                    AND levenshtein(lower(e1.naml), lower(e2.naml)) <= :lev_threshold
                WHERE e1.naml LIKE :name_pattern
                    {city_filter}
                    {state_filter}
                ORDER BY match_score DESC
                LIMIT :limit
                """
            )

            params = {
                "sim_threshold": self.similarity_threshold - 0.1,
                "lev_threshold": self.distance_threshold,
                "name_pattern": f"%{name_normalized[:50]}%",
                "limit": limit,
                **city_params,
                **state_params,
            }

            rows = conn.execute(query, params).fetchall()

        matches = []
        for row in rows:
            matches.append(
                EntityMatch(
                    source_id=row[0],
                    source_naml=row[1],
                    source_namf=row[2],
                    source_nams=row[3],
                    source_city=row[4],
                    source_st=row[5],
                    target_id=row[6],
                    target_naml=row[7],
                    target_namf=row[8],
                    target_nams=row[9],
                    target_city=row[10],
                    target_st=row[11],
                    similarity=row[12],
                    match_method="trigram+levenshtein",
                    match_score=row[12],
                )
            )

        logger.debug(
            "Found %d matches for '%s' (threshold=%.2f)",
            len(matches), name, self.similarity_threshold
        )
        return matches

    def find_matches_for_entity(
        self,
        entity_id: int,
        name: str,
        city: str | None = None,
        limit: int = 20,
    ) -> list[EntityMatch]:
        """Find all potential matches for a specific entity.

        Args:
            entity_id: The entity to find matches for.
            name: The entity's name.
            city: Optional city filter.
            limit: Maximum number of results.

        Returns:
            List of EntityMatch where source_id is the given entity_id.
        """
        all_matches = self.find_matches(name, city=city, limit=limit * 3)
        return [
            m for m in all_matches if m.source_id == entity_id
        ][:limit]

    def suggest_merge(
        self,
        entity_a_id: int,
        entity_b_id: int,
        match_score: float = 0.0,
        match_method: str = "manual",
        notes: str | None = None,
    ) -> None:
        """Add a merge suggestion to the queue.

        Args:
            entity_a_id: Primary entity (will survive the merge).
            entity_b_id: Secondary entity (will be merged into entity_a).
            match_score: Similarity score that triggered the suggestion.
            match_method: How the match was detected.
            notes: Optional notes for the reviewer.
        """
        with self._conn.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO entity_merge_queue
                        (entity_a_id, entity_b_id, match_score, match_method, notes)
                    VALUES
                        (:entity_a_id, :entity_b_id, :match_score, :match_method, :notes)
                    """
                ),
                {
                    "entity_a_id": entity_a_id,
                    "entity_b_id": entity_b_id,
                    "match_score": match_score,
                    "match_method": match_method,
                    "notes": notes,
                },
            )

        logger.info(
            "Merge suggestion: entity %d + entity %d (score=%.4f, method=%s)",
            entity_a_id, entity_b_id, match_score, match_method
        )

    def get_pending_merges(self, limit: int = 100) -> list[MergeSuggestion]:
        """Get all pending merge suggestions.

        Args:
            limit: Maximum number of results.

        Returns:
            List of MergeSuggestion objects ordered by match_score descending.
        """
        with self._conn.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT queue_id, entity_a_id, entity_b_id,
                           match_score, match_method, notes
                    FROM entity_merge_queue
                    WHERE status = 'pending'
                    ORDER BY match_score DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            ).fetchall()

        return [
            MergeSuggestion(
                queue_id=row[0],
                entity_a_id=row[1],
                entity_b_id=row[2],
                match_score=float(row[3]) if row[3] else 0.0,
                match_method=row[4] or "",
                notes=row[5],
            )
            for row in rows
        ]

    def apply_merge(self, queue_id: int, reviewed_by: str | None = None) -> None:
        """Apply a merge: merge entity_b into entity_a.

        This performs the following:
        1. Updates all references to entity_b_id to entity_a_id
           (via FK-able columns in the entity table)
        2. Merges aliases from entity_b into entity_a
        3. Marks the merge queue entry as applied

        Args:
            queue_id: The merge queue entry to apply.
            reviewed_by: Who approved this merge.
        """
        with self._conn.begin() as conn:
            # Get the merge details
            row = conn.execute(
                text(
                    """
                    SELECT entity_a_id, entity_b_id
                    FROM entity_merge_queue
                    WHERE queue_id = :queue_id
                    AND status = 'pending'
                    """
                ),
                {"queue_id": queue_id},
            ).fetchone()

            if not row:
                raise ValueError(f"Merge queue {queue_id} not found or already applied")

            entity_a_id, entity_b_id = row

            # Merge aliases: copy from entity_b to entity_a
            conn.execute(
                text(
                    """
                    INSERT INTO entity_alias (entity_id, alias_name, source_filer_id, source_table)
                    SELECT :entity_a_id, alias_name, source_filer_id, source_table
                    FROM entity_alias
                    WHERE entity_id = :entity_b_id
                      AND NOT EXISTS (
                          SELECT 1 FROM entity_alias ea2
                          WHERE ea2.entity_id = :entity_a_id
                            AND ea2.alias_name = entity_alias.alias_name
                      )
                    """
                ),
                {"entity_a_id": entity_a_id, "entity_b_id": entity_b_id},
            )

            # Mark entity_b as resolved to entity_a
            conn.execute(
                text(
                    "UPDATE entity SET resolved_by = :resolved_by WHERE entity_id = :target_id"
                ),
                {"resolved_by": entity_a_id, "target_id": entity_b_id},
            )

            # Update the merge queue
            import datetime
            conn.execute(
                text(
                    """
                    UPDATE entity_merge_queue
                    SET status = 'applied',
                        reviewed_by = :reviewed_by,
                        reviewed_at = :reviewed_at
                    WHERE queue_id = :queue_id
                    """
                ),
                {
                    "reviewed_by": reviewed_by,
                    "reviewed_at": datetime.datetime.now(datetime.timezone.utc),
                    "queue_id": queue_id,
                },
            )

        logger.info(
            "Merge applied: queue %d — entity %d absorbed into entity %d",
            queue_id, entity_b_id, entity_a_id
        )

    def reject_merge(self, queue_id: int, notes: str | None = None) -> None:
        """Reject a merge suggestion.

        Args:
            queue_id: The merge queue entry to reject.
            notes: Optional rejection notes.
        """
        with self._conn.begin() as conn:
            import datetime
            conn.execute(
                text(
                    """
                    UPDATE entity_merge_queue
                    SET status = 'rejected',
                        reviewed_at = :reviewed_at
                    WHERE queue_id = :queue_id
                      AND status = 'pending'
                    """
                ),
                {
                    "reviewed_at": datetime.datetime.now(datetime.timezone.utc),
                    "queue_id": queue_id,
                },
            )

        logger.info("Merge rejected: queue %d", queue_id)

    def get_entity_stats(self) -> dict[str, int]:
        """Get entity resolution statistics.

        Returns:
            Dict with counts: total_entities, resolved_entities,
            pending_merges, applied_merges, rejected_merges.
        """
        with self._conn.begin() as conn:
            stats = conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM entity) AS total_entities,
                        (SELECT COUNT(*) FROM entity WHERE resolved_by IS NOT NULL) AS resolved_entities,
                        (SELECT COUNT(*) FROM entity_merge_queue WHERE status = 'pending') AS pending_merges,
                        (SELECT COUNT(*) FROM entity_merge_queue WHERE status = 'applied') AS applied_merges,
                        (SELECT COUNT(*) FROM entity_merge_queue WHERE status = 'rejected') AS rejected_merges
                    """
                )
            ).fetchone()

        if not stats:
            return {
                "total_entities": 0,
                "resolved_entities": 0,
                "pending_merges": 0,
                "applied_merges": 0,
                "rejected_merges": 0,
            }

        return {
            "total_entities": stats[0] or 0,
            "resolved_entities": stats[1] or 0,
            "pending_merges": stats[2] or 0,
            "applied_merges": stats[3] or 0,
            "rejected_merges": stats[4] or 0,
        }
