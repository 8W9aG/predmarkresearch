---
name: write-paper
description: Write a research paper from completed experiments in this repo. Use when asked to write up, draft, or update a paper or research note.
---

# Write a paper

A paper reports completed experiments. It must not contain a result that has no `results.json`.

1. Choose the experiments and read their preregistration, results, and report. Check
   `research/literature/INDEX.md`. If too little on this topic has been read, say so and go through
   more papers with the user first.
2. Write `research/papers/P<NNN>-<slug>/paper.md`: abstract, introduction and contribution, related
   work, data, method, results, limitations, conclusion, references.
3. Rules:
   - Take every number directly from `results.json`, naming the experiment ID (such as `E001`) in the
     text. Do not round in a way that changes a conclusion.
   - Cite as `[@key]` from the library. Describe a paper only as far as it was actually read.
   - Separate preregistered tests from exploratory observations, and state how many were tried.
   - Report the sample flow, including how many observations each filter removed.
   - Present null results plainly. A contribution claim must be supported by what was read, so "we are
     not aware of" must never be stated as "no one has".
4. Run `uv run python -m predmark.research check-paper <path>` and fix every problem.
5. Label the status at the top: `Draft, AI-generated, not peer reviewed`.
