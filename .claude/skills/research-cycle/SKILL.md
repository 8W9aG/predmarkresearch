---
name: research-cycle
description: Run one cycle of original prediction market research, from choosing an idea through preregistration, dataset, analysis, and recorded result. Use when asked to do research, test a hypothesis, or build a new dataset or model.
---

# Research cycle

Follow the rules in `CLAUDE.md`, in particular the integrity section.

1. **Pick.** Choose from `research/ideas.md`, favouring ideas that open a new dataset or modelling
   technique or that could survive costs. Check `research/trials.jsonl` and `research/experiments/`
   so nothing is repeated by accident.
2. **Preregister.** Create `research/experiments/E<NNN>-<slug>/preregistration.md`: population,
   sampling, observation timing, filters and whether each is knowable at decision time, split,
   hypotheses with decision rules, costs, and uncertainty method. Only API schemas and metadata may
   be inspected first, and whatever was inspected must be disclosed.
3. **Build the data.** Make collection resumable and deterministic, under `data/` (not in git). Keep
   the collection code in the experiment directory, and log failures rather than dropping them.
4. **Test the analysis.** Put synthetic tests with a planted effect and a null in `tests/`, and include
   a check that nothing after decision time is used. Then have the user or a commit lock it in: commit
   before opening the holdout if committing is authorized, otherwise record the preregistration hash.
5. **Evaluate once.** Use `predmark.research.ledger`. Dev may be run freely, and each run is logged. The
   holdout runs once.
6. **Record.** Save `results.json`, and write a short `report.md` that leads with the decision on each
   hypothesis and lists what could still be wrong. Add follow-up ideas to `research/ideas.md`.
7. **Promote only with evidence.** A strategy reaches execution only after a supported holdout result,
   robust clustering, realistic costs and capacity, and then paper trading through `Engine`.
