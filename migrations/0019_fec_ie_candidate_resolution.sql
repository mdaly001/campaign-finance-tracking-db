-- 0019: Candidate name resolution for the federal IE tools.
--
-- WHY: the raw Schedule E has two problems that make per-candidate totals wrong:
--   (a) Some independent expenditures carry a candidate_name but a NULL
--       candidate_id (e.g. $278K against Campaign-Najjar in CA-48). Grouping by
--       candidate_id drops them.
--   (b) The SAME candidate_id is reported under different name ORDERINGS
--       ("WILPERT, MARNI VON" / "VON WILPERT, MARNI" / "MARNI, VON WILPERT").
--
-- FIX: a deterministic canonical-name key (uppercase, strip punctuation, sort
-- the name tokens) lets us fold NULL-id records into the candidate that shares
-- that canonical name. This is EXACT, not fuzzy — reordering tokens is a
-- lossless normalization, so it cannot merge two different people.
--
-- SAFETY GUARD: a NULL-id record is only folded when its canonical name maps to
-- EXACTLY ONE candidate_id. If two different candidates share a canonical name
-- (n_ids > 1), we leave the NULL-id record unresolved rather than guess — a
-- wrong merge is worse than an unresolved row.
--
-- Fuzzy (trigram) matching is deliberately NOT used for the fold; it belongs in
-- the find/lookup step (fec_find_candidate) where a human reviews candidates,
-- never in the aggregation merge.
--
-- MATERIALIZED: the name->id map and the display-name mode() must be computed
-- over the whole de-duplicated set. As a plain view that recomputes on every
-- query and made the tools time out. As a MATERIALIZED VIEW it is computed once
-- at refresh time and the indexes below make the tool queries fast.
--
--   >>> REFRESH MATERIALIZED VIEW CONCURRENTLY fec.fec_ie_targeted_resolved;
--   after every IE load (the UNIQUE index on ie_targeted_id enables CONCURRENT).

-- Canonical name key: uppercase, non-letters -> space, drop empties, sort tokens.
CREATE OR REPLACE FUNCTION fec.canonical_name(raw TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(string_agg(tok, ' ' ORDER BY tok), '')
    FROM unnest(
        string_to_array(upper(regexp_replace(coalesce(raw, ''), '[^A-Za-z ]', ' ', 'g')), ' ')
    ) AS tok
    WHERE tok <> '';
$$;

-- Replace the plain view (if present) with the materialized one.
DROP VIEW IF EXISTS fec.fec_ie_targeted_resolved;

CREATE MATERIALIZED VIEW fec.fec_ie_targeted_resolved AS
WITH nm AS (
    SELECT fec.canonical_name(candidate_name) AS ckey,
           min(candidate_id) AS cid,
           count(DISTINCT candidate_id) AS n_ids
    FROM fec.fec_ie_targeted_dedup
    WHERE candidate_id IS NOT NULL AND candidate_name IS NOT NULL
    GROUP BY 1
),
resolved AS (
    SELECT t.*,
           CASE
               WHEN t.candidate_id IS NOT NULL THEN t.candidate_id
               WHEN nm.n_ids = 1 THEN nm.cid   -- fold only unambiguous NULL-id rows
               ELSE NULL
           END AS resolved_candidate_id
    FROM fec.fec_ie_targeted_dedup t
    LEFT JOIN nm ON nm.ckey = fec.canonical_name(t.candidate_name)
),
disp AS (
    SELECT resolved_candidate_id,
           mode() WITHIN GROUP (ORDER BY candidate_name) AS display_name
    FROM resolved
    WHERE resolved_candidate_id IS NOT NULL
    GROUP BY resolved_candidate_id
)
SELECT r.*,
       coalesce(d.display_name, r.candidate_name) AS resolved_candidate_name
FROM resolved r
LEFT JOIN disp d ON d.resolved_candidate_id = r.resolved_candidate_id;

-- UNIQUE key enables REFRESH ... CONCURRENTLY.
CREATE UNIQUE INDEX ix_fec_ie_resolved_pk
    ON fec.fec_ie_targeted_resolved (ie_targeted_id);
-- Per-candidate aggregation (fec_ie_summary / fec_top_spending / by_target).
CREATE INDEX ix_fec_ie_resolved_cid
    ON fec.fec_ie_targeted_resolved (resolved_candidate_id);
-- Fuzzy candidate lookup (pg_trgm) for fec_find_candidate.
CREATE INDEX ix_fec_ie_resolved_name_trgm
    ON fec.fec_ie_targeted_resolved USING gin (resolved_candidate_name gin_trgm_ops);
-- Race-scoped queries (fec_race_overview).
CREATE INDEX ix_fec_ie_resolved_race
    ON fec.fec_ie_targeted_resolved (office_state, office_district, election_year);

COMMENT ON MATERIALIZED VIEW fec.fec_ie_targeted_resolved IS
    'De-duplicated Schedule E + resolved candidate key. resolved_candidate_id '
    'folds NULL-id records into their named candidate (exact canonical-name match, '
    'single-candidate guard). Use this for per-candidate totals. REFRESH '
    'MATERIALIZED VIEW CONCURRENTLY after every IE load.';
