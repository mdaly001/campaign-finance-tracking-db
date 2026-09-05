# Disclaimer — Verify before you trust or share

**This project is a pipeline and a query surface, not a source of truth.**
An LLM agent or MCP client that queries this database is reasoning from the
context it was given. When that context is wrong, so is what it tells you.

## What can go wrong

Expect these, because they will happen:

- **Bugs happen.** Table structures can be misunderstood or misrepresented by
  an agent (wrong column, wrong join, wrong grain).
- **The agent guesses from its context.** If the context is wrong, if the
  model isn't the right tool for the task, or if a table was malformed on
  load, the output will be wrong — and it will be wrong *confidently*. Tone is
  not evidence.

## What you must do

Before you rely on anything the agent reports — a figure, a name, a filing
date, an "audit finding" — **verify it against the source data**: re-run the
query, open the underlying filing, compare it to the public record.

**Laziness in verifying output is not an excuse, and not a defense for
spreading misinformation.** If you republish what an agent produced, you are
now a publisher of it, and you're accountable for it.

> **Don't be an asshole — double-check the output before you share it.**
