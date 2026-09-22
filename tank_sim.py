"""Plant simulator + episode runner shared by the live sim and the offline trainer.

Both LeakyTanke.py (live) and train_supervisor.py (offline fitness harness) call
run_episode() so a failure mode fixed in training is guaranteed to be fixed live.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from PID import PIDController
from supervisor_security import call_with_timeout


class LeakyTank:
    def __init__(self, area=2.0, outflow_coeff=0.5, initial_level=2.0):
        self.area = area
        self.c = outflow_coeff
        self.h = initial_level  # Current water level (m)
        self.d = 0.0  # Unmeasured leak (disturbance)

    def step(self, u, dt=0.1):
        gravity_out = self.c * np.sqrt(max(self.h, 0.0))
        dhdt = (u - gravity_out - self.d) / self.area
        # float() here keeps np.sqrt's np.float64 from leaking into telemetry and
        # candidate supervisor code, where it can silently produce numpy.bool_
        # results that json.dumps refuses to serialize.
        self.h = float(max(0.0, self.h + dhdt * dt))
        return self.h


@dataclass
class ScenarioConfig:
    name: str
    sim_time: float = 30.0
    dt: float = 0.1
    nominal_setpoint: float = 3.0
    leak_onset_s: Optional[float] = 5.0
    leak_magnitude: float = 3.0
    leak_offset_s: Optional[float] = 16.0
    sensor_noise_std: float = 0.0
    initial_level: float = 2.0
    safety_bounds: tuple = (0.2, 4.5)
    pid_kp: float = 2.0
    pid_ki: float = 0.5
    pid_kd: float = 0.1
    pid_output_limits: tuple = (0.0, 10.0)
    macro_cycle_steps: int = 30
    setpoint_clamp: tuple = (0.5, 4.0)
    supervisor_timeout_s: float = 0.5
    seed: Optional[int] = None


def build_log_report(episode_result: dict) -> dict:
    """Compact numeric summary of an episode, for embedding in the Layer-3 prompt
    instead of raw per-step arrays. Repurposes the previously-dead compute_textual_loss.
    """
    pump_hist = episode_result["pump_hist"]
    level_hist = episode_result["level_hist"]
    setpoint_hist = episode_result["setpoint_hist"]
    avg_u = sum(pump_hist) / len(pump_hist)
    max_u = max(pump_hist)
    tracking_error = abs(level_hist[-1] - setpoint_hist[-1])

    return {
        "scenario": episode_result["scenario"],
        "tracking_error_final": round(float(tracking_error), 3),
        "avg_actuator_effort": round(float(avg_u), 3),
        "actuator_saturated": bool(max_u > 3.5),
        "efficiency_loss_high": bool(avg_u > 2.0),
        "metrics": episode_result["metrics"],
        "failure_point_counts": _count_failure_points(episode_result["failure_points"]),
    }


def _count_failure_points(failure_points: list) -> dict:
    counts = {}
    for fp in failure_points:
        counts[fp["type"]] = counts.get(fp["type"], 0) + 1
    return counts


def run_episode(supervisor_fn: Callable, scenario: ScenarioConfig) -> dict:
    if scenario.seed is not None:
        np.random.seed(scenario.seed)

    tank = LeakyTank(initial_level=scenario.initial_level)
    pid = PIDController(
        Kp=scenario.pid_kp,
        Ki=scenario.pid_ki,
        Kd=scenario.pid_kd,
        setpoint=scenario.nominal_setpoint,
        output_limits=scenario.pid_output_limits,
    )

    active_setpoint = scenario.nominal_setpoint
    simulation_steps = int(scenario.sim_time / scenario.dt)

    time_hist, level_hist, setpoint_hist, pump_hist, leak_hist = [], [], [], [], []
    telemetry_buffer = []
    supervisor_decisions = []
    failure_points = []

    iae_accum = 0.0
    last_anomaly_flag = False

    for t_step in range(simulation_steps):
        t = round(t_step * scenario.dt, 3)

        if scenario.leak_onset_s is not None and t == scenario.leak_onset_s:
            tank.d = scenario.leak_magnitude
        if scenario.leak_offset_s is not None and t == scenario.leak_offset_s:
            tank.d = 0.0

        fault_active = tank.d != 0.0

        pid.setpoint = active_setpoint
        measurement = tank.h
        if scenario.sensor_noise_std > 0.0:
            measurement = float(measurement + np.random.normal(0.0, scenario.sensor_noise_std))

        u = float(pid.update(measurement=measurement, current_time=t))
        h = tank.step(u, scenario.dt)

        error = active_setpoint - h
        iae_accum += abs(error) * scenario.dt

        time_hist.append(t)
        level_hist.append(h)
        setpoint_hist.append(active_setpoint)
        pump_hist.append(u)
        leak_hist.append(tank.d)

        telemetry_buffer.append({
            "time": t,
            "level": round(h, 2),
            "pump_effort": round(u, 2),
            "error": round(error, 2),
        })

        if h < scenario.safety_bounds[0] or h > scenario.safety_bounds[1]:
            failure_points.append({"time_s": t, "type": "safety_violation", "detail": f"level={h:.3f}"})

        if fault_active and not last_anomaly_flag:
            failure_points.append({"time_s": t, "type": "missed_anomaly", "detail": "fault active, last anomaly_flag=False"})
        elif not fault_active and last_anomaly_flag:
            failure_points.append({"time_s": t, "type": "false_positive", "detail": "no fault, last anomaly_flag=True"})

        if t_step > 0 and t_step % scenario.macro_cycle_steps == 0:
            window = telemetry_buffer[-scenario.macro_cycle_steps:]
            (decision, err) = call_with_timeout(
                supervisor_fn, (window, active_setpoint, scenario.nominal_setpoint),
                timeout_s=scenario.supervisor_timeout_s,
            )
            if err is not None:
                fp_type = "timeout" if err == "timeout" else "exception"
                failure_points.append({"time_s": t, "type": fp_type, "detail": err})
                decision = {
                    "diagnosis": f"SUPERVISOR_FAILURE: {err}. Preserving setpoint.",
                    "adjusted_setpoint": active_setpoint,
                    "anomaly_flag": True,
                }

            proposed_sp = float(decision["adjusted_setpoint"])
            decision_anomaly_flag = bool(decision["anomaly_flag"])

            supervisor_decisions.append({
                "time_s": t,
                "water_level_m": round(tank.h, 3),
                "pump_effort": round(u, 3),
                "adjusted_setpoint": proposed_sp,
                "anomaly_flag": decision_anomaly_flag,
                "diagnosis": str(decision["diagnosis"]),
            })

            active_setpoint = max(scenario.setpoint_clamp[0], min(scenario.setpoint_clamp[1], proposed_sp))
            last_anomaly_flag = decision_anomaly_flag

    violation_count = sum(1 for f in failure_points if f["type"] == "safety_violation")
    missed_anomaly_count = sum(1 for f in failure_points if f["type"] == "missed_anomaly")
    false_positive_count = sum(1 for f in failure_points if f["type"] == "false_positive")
    exception_count = sum(1 for f in failure_points if f["type"] in ("exception", "timeout"))

    # Only meaningful when the fault has actually cleared by episode end (or never
    # occurred) - a scenario whose leak never turns off shouldn't be penalized for
    # leaving the setpoint lowered, since that's the correct response there.
    fault_active_at_end = bool(leak_hist and leak_hist[-1] != 0.0)
    restore_gap = 0.0 if fault_active_at_end else abs(active_setpoint - scenario.nominal_setpoint)

    return {
        "scenario": scenario.name,
        "time_hist": time_hist,
        "level_hist": level_hist,
        "setpoint_hist": setpoint_hist,
        "pump_hist": pump_hist,
        "leak_hist": leak_hist,
        "supervisor_decisions": supervisor_decisions,
        "failure_points": failure_points,
        "metrics": {
            "iae": round(iae_accum, 3),
            "violation_count": violation_count,
            "missed_anomaly_count": missed_anomaly_count,
            "false_positive_count": false_positive_count,
            "exception_count": exception_count,
            "restore_gap": round(restore_gap, 3),
        },
    }
