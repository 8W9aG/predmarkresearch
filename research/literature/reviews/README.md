# Literature reviews

One page per paper or post. Each page has the citation, a summary written from the source, the places where the claims go beyond the evidence, and a separate discussion of how this repo can or can't use the work.

Records come from `research/literature/library.jsonl` in index order (`python scripts/lit.py next`). After a page is written, the record's status in the library shows what was actually read: `abstract_read` or `full_text_read`. Each page's **Read** line says which version and which parts.

Status marks how the work relates to this repo:

- **used**: code, an experiment or a skill depends on it.
- **planned**: a `TODO.md` item or an entry in `research/ideas.md` would use it.
- **not applicable**: reviewed, and this repo has no use for it as a method. The page says why, and what carries over.

## LLM forecasting and agents

| Page | Key | Status | Why |
| --- | --- | --- | --- |
| [Adaptive Auto-Harness](adaptive-auto-harness.md) (Liu et al., 2026) | `liu2026-8a82` | not applicable | An agent-engineering paper with a Polymarket replay that can leak outcomes, scores side-picking rather than probability, and has no fees. It supports E003's forward-only design. Coverage and growing agent instructions are the lessons that carry over. |
