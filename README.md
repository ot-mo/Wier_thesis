# Wier Thesis: LLM Heuristic Learner as an Industrial Supervisory Controller

Master's thesis testbed exploring whether an LLM, used **offline** as a heuristic
program-synthesis engine, can produce deterministic supervisory control logic
that approaches the performance of more principled controllers (e.g. TD-MPC)
used in mining process control — while remaining human-readable, auditable,
and security-checkable in a way a learned black-box policy is not.

## Architecture: 3 layers

```
 Layer 3 (offline, LLM)        Layer 2 (online, deterministic)    Layer 1 (online, deterministic)
 ─────────────────────         ────────────────────────────       ──────────────────────────────
 DeepSeek reads logs +    -->  supervise(telemetry, setpoint, --> PIDController tracks the
 failure-point catalog,        nominal) -> new setpoint(s) +      active setpoint against the
 proposes new supervisor       anomaly flag(s), every macro       plant every fast timestep
 source code                   cycle (~3s)                        (0.1s)
      |                             ^
      v                             |
 security check (AST          hot-loaded from
 denylist + restricted        current_supervisor.py
 exec + timeout)              at process start
      |
      v
 evaluated on a fixed
 fault-injection battery,
 promoted only if it beats
 the champion (elitist)
```

- **Layer 1 — PID** ([PID.py](PID.py)): a standard `PIDController` (anti-windup via
  integral back-off) driving each plant's actuator(s) every fast timestep. Never
  regenerated or touched by the LLM.
- **Layer 2 — deterministic supervisor**: a plain Python function,
  `supervise(...)`, hot-loaded from a `current_supervisor.py` file and run every
  macro cycle (~3s) inside the live simulation. It has no network access and
  makes no LLM calls at runtime — it's just thresholds/heuristics over a
  telemetry window, deciding whether to flag an anomaly and what setpoint(s) to
  use next.
- **Layer 3 — offline LLM learner**: run manually, *never* from the live sim. It
  reads the current supervisor's source, a compact performance report across a
  fixed battery of fault-injection scenarios, and a running catalog of past
  failure points, then asks DeepSeek to propose a new `supervise` implementation.
  Every candidate must pass a security check and strictly improve on the
  incumbent (elitism) before being promoted.

This separation means the LLM's cost, latency, and reliability never touch the
real-time control loop — its only job is to author code that a fast,
deterministic layer then executes.

## Two testbeds

| | Single-tank (SISO) | Two-tank cascade (MIMO) |
|---|---|---|
| Plant | [tank_sim.py](tank_sim.py) | [two_tank_sim.py](two_tank_sim.py) |
| Live entrypoint | [LeakyTanke.py](LeakyTanke.py) | *(no live entrypoint yet — trainer/benchmark only)* |
| Offline trainer | [train_supervisor.py](train_supervisor.py) | [train_supervisor_two_tank.py](train_supervisor_two_tank.py) |
| Benchmark vs. PID-only | [benchmark_baselines.py](benchmark_baselines.py) | [benchmark_two_tank.py](benchmark_two_tank.py) |
| Generated supervisors | `generated_supervisors_leaky_tank/` | `generated_supervisors_two_tank/` |
| Supervisor signature | `supervise(telemetry_window, active_setpoint, nominal_target)` | `supervise(telemetry_window, active_setpoints, nominal_targets)` (per-tank dicts) |

**Single-tank**: one `LeakyTank` with a pump actuator and an unmeasured leak
disturbance. The simplest possible testbed for the 3-layer pattern.

**Two-tank cascade**: Tank 1 drains via gravity into Tank 2, so a fault in
either tank measurably propagates to the other (confirmed empirically — a
tank1-only fault alone swings tank2's level by over 2m with zero fault of its
own). Each tank has its own PID/actuator; the supervisor's job is genuine
cross-loop coordination, not two independent single-loop controllers. This is
the first step toward the multivariable, coupled dynamics real mining unit
operations exhibit.

## Security sandbox

All LLM-generated `supervise` code passes through [supervisor_security.py](supervisor_security.py)
before ever running against real telemetry:

1. **`check_source`** — static AST check: no imports, no `exec`/`eval`/`open`,
   no dunder/attribute sandbox-escape tricks, exactly one top-level `def
   supervise(...)` with the required signature.
2. **`safe_exec_supervisor`** — executes in a restricted namespace with a small
   explicit set of safe builtins (arithmetic, comparisons, `math`/`statistics`)
   instead of Python's real `__builtins__`.
3. **`call_with_timeout`** — every invocation (including the one-time load) runs
   in a daemon thread with a wall-clock timeout, since Windows has no
   `signal.alarm` and a runaway candidate (`while True: pass`) must not hang
   the caller.

[test_supervisor_security.py](test_supervisor_security.py) exercises this
adversarially (import injection, `open()`, a `__class__.__bases__` sandbox
escape, an infinite loop).

## Fitness function & anti-gaming guards

Candidates are scored across a fixed battery of fault-injection scenarios
(no-fault baseline, single leaks of varying onset/magnitude/duration, sensor
noise, simultaneous faults). The score combines tracking error (IAE), safety
violations, missed anomalies, false positives, exceptions/timeouts, and a
restoration gap (did the setpoint return to nominal after the fault cleared?).

Two things learned the hard way and now built in:

- **A false positive in a scenario with zero fault ever is penalized far more
  harshly** than one during genuine fault ambiguity — otherwise a supervisor
  can "solve" hard scenarios by flagging anomalies constantly and only pays a
  cheap, uniform price for it.
- **A candidate is only promoted if it doesn't regress any individual
  scenario beyond tolerance**, even if the *average* score improves. Averaging
  across a scenario battery otherwise lets a candidate win in aggregate by
  badly breaking one scenario (especially a provably fault-free one) while
  gaining disproportionately elsewhere — a composite-score exploit
  structurally identical to reward hacking in RL.

## Running it

```bash
# Live single-tank simulation (opens a plot window)
python LeakyTanke.py

# Offline trainer (makes real, billed DeepSeek API calls)
python train_supervisor.py [num_trials]        # single-tank
python train_supervisor_two_tank.py [num_trials]  # two-tank

# Compare the current trained supervisor against a PID-only baseline
python benchmark_baselines.py     # single-tank
python benchmark_two_tank.py      # two-tank

# Security/sandbox tests
python test_supervisor_security.py
```

Requires a `.env` file with `DEEPSEEK_API_KEY` set (only needed for the
trainer scripts — the live sim and benchmarks make no network calls).

## Status / open threads

- Single-tank supervisor has gone through ~60 generations; current champion
  detects and recovers from all battery scenarios and restores to nominal
  after a fault clears.
- Two-tank supervisor reliably solves single-tank-at-a-time faults, but
  simultaneous faults on both tanks remain unsolved: every attempt so far
  that improves joint-fault detection breaks robustness to sensor noise, and
  the model has been repeating near-identical proposals across trials rather
  than exploring new approaches — a likely local-optimum/search-diversity
  issue worth addressing before more trials.
- No RL/TD-MPC baseline yet; `benchmark_baselines.py`'s pattern (same
  scenario battery, same scoring) is the intended extension point once one
  exists, and eventually against the mining company's real controller and
  data.

