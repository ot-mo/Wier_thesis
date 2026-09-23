"""Layer 3: offline DeepSeek-driven heuristic learner for the Layer-2 supervisor.

Run manually: python train_supervisor.py [num_trials]

Reads the current supervisor + a fixed fault-injection scenario battery + a
running failure-point catalog, asks DeepSeek to propose new Layer-2 source,
security-checks it, evaluates it, and promotes it only on a strict score
improvement (elitism, mirroring LLM_evo.py's hill-climb). Never invoked from
the live sim.
"""

import json
import os
import sys
import time

from openai import OpenAI
from dotenv import load_dotenv

from supervisor_security import check_source, safe_exec_supervisor
from tank_sim import ScenarioConfig, run_episode, build_log_report

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

SUPERVISORS_DIR = "generated_supervisors_leaky_tank"
CURRENT_SUPERVISOR_PATH = os.path.join(SUPERVISORS_DIR, "current_supervisor.py")
RESULTS_DIR = "results"
FAILURE_POINTS_PATH = os.path.join(RESULTS_DIR, "failure_points.jsonl")
TRIALS_PATH = os.path.join(RESULTS_DIR, "supervisor_training_trials.jsonl")
CONTEXT_REPORT_PATH = os.path.join(RESULTS_DIR, "context_report.jsonl")
MAX_CONTEXT_RELATIONS = 25  # bounds prompt growth over a long run

VIOLATION_PENALTY = 500
MISSED_ANOMALY_PENALTY = 300
FALSE_POSITIVE_PENALTY = 160
NO_FAULT_FALSE_POSITIVE_PENALTY = 800  # scenarios with zero fault ever - no excuse to trigger
EXCEPTION_PENALTY = 10000
RESTORE_GAP_PENALTY = 200

# Guards against a candidate "winning" the averaged score by regressing badly on
# one scenario while gaining on others (composite-score gaming) - a promotion
# is rejected if any individual scenario got worse than tolerance versus the
# current champion's own score on that scenario, even if the total improved.
# Tolerance is asymmetric: a provably fault-free scenario gets near-zero
# tolerance (any regression there is inexcusable - that's the exact exploit
# this guard exists for), while a scenario with a genuine fault gets a looser
# tolerance, since trading a little performance on one real fault scenario for
# a much bigger win on another is a normal, legitimate control-engineering
# trade-off, not gaming.
NO_FAULT_REGRESSION_ABS_TOLERANCE = 5.0
FAULT_REGRESSION_ABS_TOLERANCE = 100.0
FAULT_REGRESSION_REL_TOLERANCE = 0.15

SCENARIO_BATTERY = [
    ScenarioConfig(name="baseline_no_fault", leak_onset_s=None, leak_offset_s=None),
    ScenarioConfig(name="standard_leak"),  # today's default: onset 5.0, magnitude 3.0, offset 16.0
    ScenarioConfig(name="early_severe_leak", leak_onset_s=2.0, leak_magnitude=5.0, leak_offset_s=None),
    ScenarioConfig(name="late_mild_leak", leak_onset_s=20.0, leak_magnitude=1.0, leak_offset_s=None),
    ScenarioConfig(name="leak_on_off_short", leak_onset_s=4.0, leak_magnitude=2.0, leak_offset_s=8.0),
    ScenarioConfig(name="leak_on_off_long", leak_onset_s=3.0, leak_magnitude=2.5, leak_offset_s=22.0),
    ScenarioConfig(name="noisy_sensor_no_fault", leak_onset_s=None, leak_offset_s=None, sensor_noise_std=0.1, seed=42),
    ScenarioConfig(name="noisy_sensor_with_leak", sensor_noise_std=0.1, seed=42),
]


def score_supervisor(supervisor_fn):
    """Returns (avg_score, per_scenario_traces)."""
    traces = []
    total = 0.0
    for scenario in SCENARIO_BATTERY:
        result = run_episode(supervisor_fn, scenario)
        m = result["metrics"]
        fp_penalty = NO_FAULT_FALSE_POSITIVE_PENALTY if scenario.leak_onset_s is None else FALSE_POSITIVE_PENALTY
        scenario_score = (
            m["iae"]
            + m["violation_count"] * VIOLATION_PENALTY
            + m["missed_anomaly_count"] * MISSED_ANOMALY_PENALTY
            + m["false_positive_count"] * fp_penalty
            + m["exception_count"] * EXCEPTION_PENALTY
            + m["restore_gap"] * RESTORE_GAP_PENALTY
        )
        total += scenario_score
        traces.append({"scenario": scenario.name, "score": round(scenario_score, 3), "log_report": build_log_report(result)})
    return total / len(SCENARIO_BATTERY), traces


def load_failure_catalog(max_examples_per_category=3):
    if not os.path.exists(FAILURE_POINTS_PATH):
        return {"counts": {}, "examples": {}}

    counts = {}
    examples = {}
    with open(FAILURE_POINTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            fp_type = entry.get("type", "unknown")
            counts[fp_type] = counts.get(fp_type, 0) + 1
            examples.setdefault(fp_type, [])
            if len(examples[fp_type]) < max_examples_per_category:
                examples[fp_type].append(entry)
    return {"counts": counts, "examples": examples}


def load_context_report(max_relations=MAX_CONTEXT_RELATIONS):
    """Reads accumulated cause-effect relations discovered across past trials.

    Distinct from failure_catalog (raw counts) and per-trial failure_analysis
    (about one specific candidate): this is durable, reusable knowledge that
    should inform every future trial, not just the one that discovered it.
    Without this, each trial reasons from scratch and can re-derive (or get
    stuck repeating) the same failed trade-off with no memory of having tried
    it before.
    """
    if not os.path.exists(CONTEXT_REPORT_PATH):
        return []
    relations = []
    with open(CONTEXT_REPORT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            for relation in entry.get("relations", []):
                relations.append(relation)
    return relations[-max_relations:]


def append_context_report(trial_idx, relations):
    if not relations:
        return
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(CONTEXT_REPORT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"trial": trial_idx, "relations": relations}) + "\n")


def build_prompt(current_code, best_score, traces, failure_catalog, context_relations):
    scenario_names = ", ".join(s.name for s in SCENARIO_BATTERY)
    return f"""
You are improving a deterministic supervisory control function for a leaky water tank.

The function signature MUST remain exactly:
def supervise(telemetry_window, active_setpoint, nominal_target):
    ...
    return {{"diagnosis": str, "adjusted_setpoint": float, "anomaly_flag": bool}}

Rules for the code you write:
- No imports, no exec/eval, no file/network/os access, no access to dunder attributes.
- Only use: arithmetic, comparisons, built-in functions (abs, min, max, len, round, sum, sorted, range, etc.), and the `math`/`statistics` modules (already available, do not import them).
- The function must be a pure function of its three arguments plus module-level constants; it will be re-loaded fresh each run, so no persistent state across calls.

Current supervisor source:
```python
{current_code}
```

Current average score across the fixed scenario battery ({scenario_names}): {best_score}
(Lower score is better. Score = IAE + violation_count*{VIOLATION_PENALTY} + missed_anomaly_count*{MISSED_ANOMALY_PENALTY} + false_positive_count*FP_PENALTY + exception_count*{EXCEPTION_PENALTY} + restore_gap*{RESTORE_GAP_PENALTY})
(restore_gap is |final_setpoint - nominal_target| measured only in scenarios where the fault has fully cleared by the end of the run - it penalizes leaving the setpoint lowered after a fault is no longer present, and is 0 for scenarios whose fault never clears.)
(FP_PENALTY is {NO_FAULT_FALSE_POSITIVE_PENALTY} in scenarios that never inject any fault at all - baseline_no_fault, noisy_sensor_no_fault - since there is zero excuse to ever flag an anomaly there, and {FALSE_POSITIVE_PENALTY} elsewhere.)
(IMPORTANT: a candidate is only promoted if it improves the average score AND does not regress any individual scenario beyond tolerance versus the current champion, even if the average improves. Tolerance is asymmetric: for baseline_no_fault/noisy_sensor_no_fault (no fault ever), tolerance is ~0 - any regression there is rejected outright. For scenarios with a genuine fault, up to ~15% (or 100 points, whichever is larger) of regression is allowed, so a trade-off like "fix a hard scenario at a small cost to an easy one" is fine as long as it stays within that band.)

Per-scenario results with the current supervisor:
{json.dumps(traces, indent=2)}

Aggregate failure-point catalog from past runs (counts and worst examples):
{json.dumps(failure_catalog, indent=2)}

Lessons learned from previous trials - cause-effect relationships already discovered
by earlier attempts (these are durable observations, not tied to any one candidate's
code; do not repeat a change that a relation below says already failed for a known
reason - build on this instead of re-deriving it from scratch):
{json.dumps(context_relations, indent=2) if context_relations else "(none recorded yet - this is an early trial)"}

Task:
1. Diagnose what is causing the worst-scoring scenarios and/or the most common failure-point categories.
2. Propose an improved `supervise` function that reduces missed anomalies and false positives without introducing new safety violations, and that restores the setpoint back toward nominal_target once a fault has genuinely cleared. Take the lessons-learned list into account - if it shows a class of fix has already failed for a specific reason, try a genuinely different approach instead of a small variation on it.
3. Separately from the code change itself, identify any NEW generalizable cause-effect relationship this trial's result reveals (e.g. "X tends to cause Y because Z") that would be useful for future trials to know, whether or not this candidate gets promoted. Only include a relation if it is a genuinely new, durable insight - do not repeat one already listed above.

You must output strictly JSON matching this structure:
{{
  "failure_analysis": {{
    "what_failed": "string",
    "failing_scenarios": ["scenario_name", ...],
    "change_type": "structural | scalar/config | bug_fix",
    "next_recommendation": "string"
  }},
  "proposed_change": "string",
  "code": "full source of the new supervise function as a string",
  "relations_learned": ["short generalizable cause-effect statement", ...]
}}
"""


def call_deepseek(prompt, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            time.sleep(0.3)
            response = client.chat.completions.create(
                model="deepseek-flash",
                messages=[
                    {"role": "system", "content": "You are a control-systems engineer. Respond ONLY with valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"[DEEPSEEK API ERROR - Attempt {attempt}/{max_retries}]: {e}")
            time.sleep(1.0 * attempt)
    print(f"[CRITICAL] All {max_retries} retries failed for this trial.")
    return None


def promote(candidate_code, trial_idx):
    gen_path = os.path.join(SUPERVISORS_DIR, f"supervisor_gen_{trial_idx}.py")
    best_path = os.path.join(SUPERVISORS_DIR, f"best_supervisor_gen_{trial_idx}.py")
    with open(gen_path, "w", encoding="utf-8") as f:
        f.write(candidate_code)
    with open(best_path, "w", encoding="utf-8") as f:
        f.write(candidate_code)
    with open(CURRENT_SUPERVISOR_PATH, "w", encoding="utf-8") as f:
        f.write(candidate_code)
    print(f"[PROMOTED] Trial {trial_idx} is the new current_supervisor.py")


def save_candidate_only(candidate_code, trial_idx):
    gen_path = os.path.join(SUPERVISORS_DIR, f"supervisor_gen_{trial_idx}.py")
    with open(gen_path, "w", encoding="utf-8") as f:
        f.write(candidate_code)


def log_trial(trial_idx, decision, score, failure_analysis, proposed_change, reason=None):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(TRIALS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "trial": trial_idx,
            "decision": decision,
            "score": score,
            "failure_analysis": failure_analysis,
            "proposed_change": proposed_change,
            "reason": reason,
        }) + "\n")


def append_failure_points(traces, run_label):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(FAILURE_POINTS_PATH, "a", encoding="utf-8") as f:
        for trace in traces:
            for fp_type, count in trace["log_report"]["failure_point_counts"].items():
                f.write(json.dumps({
                    "run": run_label,
                    "scenario": trace["scenario"],
                    "time_s": None,
                    "type": fp_type,
                    "detail": f"count={count} in this evaluation",
                }) + "\n")


_SCENARIO_BY_NAME = {s.name: s for s in SCENARIO_BATTERY}


def find_scenario_regression(best_traces, cand_traces):
    """Returns (scenario_name, old_score, new_score) for the first scenario where
    the candidate is worse than the champion beyond tolerance, or None if none.
    Guards against composite-score gaming: a lower aggregate score can otherwise
    hide a severe regression on one scenario offset by gains on others.
    """
    for b, c in zip(best_traces, cand_traces):
        scenario = _SCENARIO_BY_NAME[b["scenario"]]
        if scenario.leak_onset_s is None:
            allowed = NO_FAULT_REGRESSION_ABS_TOLERANCE
        else:
            allowed = max(FAULT_REGRESSION_ABS_TOLERANCE, FAULT_REGRESSION_REL_TOLERANCE * b["score"])
        if c["score"] > b["score"] + allowed:
            return b["scenario"], b["score"], c["score"]
    return None


def _next_trial_start():
    """Scans SUPERVISORS_DIR for existing supervisor_gen_N.py files so a fresh
    invocation continues numbering instead of restarting at 1 and silently
    overwriting a previous run's audit trail (gen files are not run-scoped).
    """
    existing = [f for f in os.listdir(SUPERVISORS_DIR) if f.startswith("supervisor_gen_") and f.endswith(".py")]
    max_n = 0
    for f in existing:
        try:
            n = int(f[len("supervisor_gen_"):-len(".py")])
            max_n = max(max_n, n)
        except ValueError:
            continue
    return max_n + 1


def main():
    num_trials = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    start_trial = _next_trial_start()

    with open(CURRENT_SUPERVISOR_PATH, "r", encoding="utf-8") as f:
        current_code = f.read()

    ok, reason = check_source(current_code)
    if not ok:
        print(f"[CRITICAL] current_supervisor.py fails its own security check: {reason}")
        sys.exit(1)
    current_fn, err = safe_exec_supervisor(current_code)
    if err:
        print(f"[CRITICAL] current_supervisor.py failed to load: {err}")
        sys.exit(1)

    best_score, best_traces = score_supervisor(current_fn)
    append_failure_points(best_traces, "trainer_baseline")
    print(f"[BASELINE] current_supervisor.py avg score: {best_score:.3f}")

    for i in range(num_trials):
        trial_idx = start_trial + i
        print(f"\n=== Trial {i + 1}/{num_trials} (gen_{trial_idx}) ===")
        failure_catalog = load_failure_catalog()
        context_relations = load_context_report()
        prompt = build_prompt(current_code, best_score, best_traces, failure_catalog, context_relations)
        response = call_deepseek(prompt)

        if response is None:
            log_trial(trial_idx, "SKIPPED", None, None, None, reason="DeepSeek call failed")
            continue

        candidate_code = response.get("code", "")
        failure_analysis = response.get("failure_analysis")
        proposed_change = response.get("proposed_change")
        new_relations = response.get("relations_learned") or []
        if new_relations:
            print(f"[LEARNED] {new_relations}")
        append_context_report(trial_idx, new_relations)

        ok, reason = check_source(candidate_code)
        if not ok:
            print(f"[REJECTED - SECURITY] {reason}")
            log_trial(trial_idx, "REJECTED_SECURITY", None, failure_analysis, proposed_change, reason=reason)
            continue

        candidate_fn, err = safe_exec_supervisor(candidate_code)
        if err:
            print(f"[REJECTED - LOAD] {err}")
            log_trial(trial_idx, "REJECTED_LOAD", None, failure_analysis, proposed_change, reason=err)
            continue

        cand_score, cand_traces = score_supervisor(candidate_fn)
        append_failure_points(cand_traces, f"trainer_trial_{trial_idx}")
        print(f"Candidate score: {cand_score:.3f} (current best: {best_score:.3f})")

        if cand_score < best_score:
            regression = find_scenario_regression(best_traces, cand_traces)
            if regression:
                scen_name, old_s, new_s = regression
                print(f"[REJECTED - REGRESSION] '{scen_name}' regressed {old_s:.1f} -> {new_s:.1f} despite a better aggregate score")
                save_candidate_only(candidate_code, trial_idx)
                log_trial(trial_idx, "REJECTED_REGRESSION", cand_score, failure_analysis, proposed_change,
                          reason=f"{scen_name} regressed {old_s:.1f} -> {new_s:.1f}")
                continue
            promote(candidate_code, trial_idx)
            best_score, current_code, best_traces = cand_score, candidate_code, cand_traces
            log_trial(trial_idx, "PROMOTED", cand_score, failure_analysis, proposed_change)
        else:
            save_candidate_only(candidate_code, trial_idx)
            log_trial(trial_idx, "ROLLBACK", cand_score, failure_analysis, proposed_change)

    print(f"\n[DONE] Final best score: {best_score:.3f}. current_supervisor.py reflects the best candidate found.")


if __name__ == "__main__":
    main()
