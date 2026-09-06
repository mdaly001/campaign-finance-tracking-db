-- 0007: Transaction-level dedup for fact-table views (issue #4).
--
-- Migration 0004 keyed dedup on (filing_id, line_item): the latest amend
-- version per filing-line slot. When a transaction is amended, the amended
-- version is refiled on a NEW line_item of the amended filing, so one logical
-- transaction (same cmte_id + tran_id) survived dedup in several slots and was
-- summed once per slot — inflating vendor/payee totals (observed ~2x).
--
-- The logical transaction is (cmte_id, tran_id); its current value is the
-- version with the highest amend_id. This migration re-keys the fact views
-- that carry a committee column to that key: each transaction now survives
-- exactly once — as its current version, attributed to the filing that last
-- amended it — and SUM/COUNT over a committee's filings is exact.
--
-- Rows with no committee or no transaction id (cmte_id or tran_id null/blank)
-- cannot be attributed to a transaction; they keep the old slot semantics
-- (latest version per (filing_id, line_item)) so nothing is silently dropped
-- and nothing is double-counted.
--
-- s496_cd_deduped and splt_cd_deduped deliberately keep the (filing_id,
-- line_item) key: those tables have no cmte_id column, so their rows cannot
-- be attributed to a transaction owner.

CREATE OR REPLACE VIEW expn_cd_deduped AS
SELECT * FROM (
    SELECT DISTINCT ON (cmte_id, tran_id) *
    FROM expn_cd
    WHERE cmte_id IS NOT NULL AND tran_id IS NOT NULL AND btrim(tran_id) <> ''
    ORDER BY cmte_id, tran_id, amend_id DESC, filing_id, line_item
) AS current_version
UNION ALL
SELECT * FROM (
    SELECT DISTINCT ON (filing_id, line_item) *
    FROM expn_cd
    WHERE cmte_id IS NULL OR tran_id IS NULL OR btrim(tran_id) = ''
    ORDER BY filing_id, line_item, amend_id DESC
) AS unattributable;

CREATE OR REPLACE VIEW rcpt_cd_deduped AS
SELECT * FROM (
    SELECT DISTINCT ON (cmte_id, tran_id) *
    FROM rcpt_cd
    WHERE cmte_id IS NOT NULL AND tran_id IS NOT NULL AND btrim(tran_id) <> ''
    ORDER BY cmte_id, tran_id, amend_id DESC, filing_id, line_item
) AS current_version
UNION ALL
SELECT * FROM (
    SELECT DISTINCT ON (filing_id, line_item) *
    FROM rcpt_cd
    WHERE cmte_id IS NULL OR tran_id IS NULL OR btrim(tran_id) = ''
    ORDER BY filing_id, line_item, amend_id DESC
) AS unattributable;

CREATE OR REPLACE VIEW s497_cd_deduped AS
SELECT * FROM (
    SELECT DISTINCT ON (cmte_id, tran_id) *
    FROM s497_cd
    WHERE cmte_id IS NOT NULL AND tran_id IS NOT NULL AND btrim(tran_id) <> ''
    ORDER BY cmte_id, tran_id, amend_id DESC, filing_id, line_item
) AS current_version
UNION ALL
SELECT * FROM (
    SELECT DISTINCT ON (filing_id, line_item) *
    FROM s497_cd
    WHERE cmte_id IS NULL OR tran_id IS NULL OR btrim(tran_id) = ''
    ORDER BY filing_id, line_item, amend_id DESC
) AS unattributable;

CREATE OR REPLACE VIEW s498_cd_deduped AS
SELECT * FROM (
    SELECT DISTINCT ON (cmte_id, tran_id) *
    FROM s498_cd
    WHERE cmte_id IS NOT NULL AND tran_id IS NOT NULL AND btrim(tran_id) <> ''
    ORDER BY cmte_id, tran_id, amend_id DESC, filing_id, line_item
) AS current_version
UNION ALL
SELECT * FROM (
    SELECT DISTINCT ON (filing_id, line_item) *
    FROM s498_cd
    WHERE cmte_id IS NULL OR tran_id IS NULL OR btrim(tran_id) = ''
    ORDER BY filing_id, line_item, amend_id DESC
) AS unattributable;

CREATE OR REPLACE VIEW loan_cd_deduped AS
SELECT * FROM (
    SELECT DISTINCT ON (cmte_id, tran_id) *
    FROM loan_cd
    WHERE cmte_id IS NOT NULL AND tran_id IS NOT NULL AND btrim(tran_id) <> ''
    ORDER BY cmte_id, tran_id, amend_id DESC, filing_id, line_item
) AS current_version
UNION ALL
SELECT * FROM (
    SELECT DISTINCT ON (filing_id, line_item) *
    FROM loan_cd
    WHERE cmte_id IS NULL OR tran_id IS NULL OR btrim(tran_id) = ''
    ORDER BY filing_id, line_item, amend_id DESC
) AS unattributable;

CREATE OR REPLACE VIEW debt_cd_deduped AS
SELECT * FROM (
    SELECT DISTINCT ON (cmte_id, tran_id) *
    FROM debt_cd
    WHERE cmte_id IS NOT NULL AND tran_id IS NOT NULL AND btrim(tran_id) <> ''
    ORDER BY cmte_id, tran_id, amend_id DESC, filing_id, line_item
) AS current_version
UNION ALL
SELECT * FROM (
    SELECT DISTINCT ON (filing_id, line_item) *
    FROM debt_cd
    WHERE cmte_id IS NULL OR tran_id IS NULL OR btrim(tran_id) = ''
    ORDER BY filing_id, line_item, amend_id DESC
) AS unattributable;
