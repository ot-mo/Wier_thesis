# Working conventions

- Commit changes as you go while working, not just at the end of a session. The user wants a git history that reflects the actual steps taken (e.g. one commit per logical change: a refactor, a bug fix found during verification, a cleanup pass), not one giant squashed commit at the end.
- Push to origin/main automatically after each commit, without asking for confirmation first. Confirmed 2026-09-22.

# Common commands

- Run the live sim (opens a plot window): `python LeakyTanke.py`
- Run the live sim headlessly / from an agent, without blocking on the plot window: `MPLBACKEND=Agg python LeakyTanke.py`
- Run the offline Layer-3 trainer (makes real, billed DeepSeek API calls — confirm with the user first): `python train_supervisor.py [num_trials]` (defaults to 10 trials if omitted)
- Run the supervisor security/sandbox tests: `python test_supervisor_security.py`
