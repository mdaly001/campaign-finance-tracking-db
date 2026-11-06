-- 0022: Indexes to accelerate the expn_cd_deduped view's global dedup.
--
-- The expn_cd_deduped view (read by every state-side expenditure tool and by
-- ca_race_overview) dedups the ENTIRE expn_cd table (~16M rows) via two
-- DISTINCT ON branches:
--   attributable   : DISTINCT ON (cmte_id, tran_id)
--                    ORDER BY cmte_id, tran_id, amend_id DESC, filing_id, line_item
--   unattributable : DISTINCT ON (filing_id, line_item)
--                    ORDER BY filing_id, line_item, amend_id DESC
-- Without indexes matching those ORDER BY prefixes, each branch falls back to a
-- full sort of the table: the view scan took ~11s and a full ca_race_overview
-- query ~11.5s. With these indexes the view scan drops to ~6.5s and the full
-- query to ~7s, keeping the tool inside the 15s run_sql budget.
--
-- NOTE: the migration runner executes each file inside a transaction
-- (core/migrations/migrate.py `engine.begin()`), so this uses plain
-- `CREATE INDEX IF NOT EXISTS` (a brief ShareLock on expn_cd) rather than
-- CONCURRENTLY, which cannot run inside a transaction block. On prod the two
-- indexes were first built CONCURRENTLY on 2026-11-03; `IF NOT EXISTS` makes
-- this a no-op there and only pays the lock on a fresh database.

CREATE INDEX IF NOT EXISTS idx_expn_cd_cmte_tran_amend
    ON expn_cd (cmte_id, tran_id, amend_id DESC, filing_id, line_item)
    WHERE cmte_id IS NOT NULL AND tran_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_expn_cd_filing_line_amend
    ON expn_cd (filing_id, line_item, amend_id DESC)
    WHERE cmte_id IS NULL OR tran_id IS NULL;
