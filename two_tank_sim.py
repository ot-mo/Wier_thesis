"""Two-tank cascade plant + episode runner: a MIMO supervisory control testbed.

Tank 1 drains via gravity into Tank 2 (an interacting-tanks-in-series
topology), so a disturbance in either tank propagates to the other. Each
tank has its own pump actuator and its own low-level PID; the supervisor's
job is to coordinate both setpoints rather than tune one loop in isolation.
Mirrors tank_sim.py's structure/interfaces so the same
training/benchmarking patterns extend directly to two outputs.

MIMO supervisor interface (distinct from tank_sim's single-tank one):
    def supervise(telemetry_window, active_setpoints, nominal_targets):
        ...
        return {
            "diagnosis": str,
            "adjusted_setpoints": {"tank1": float, "tank2": float},
            "anomaly_flags": {"tank1": bool, "tank2": bool},
        }
Use supervisor_security.check_source(source, required_args=MIMO_REQUIRED_ARGS)
to validate candidates against this signature instead of the SISO one.
"""

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from PID import PIDController
from supervisor_security import call_with_timeout

MIMO_REQUIRED_ARGS = ("telemetry_window", "active_setpoints", "nominal_targets")


class TwoTankSystem:
    def __init__(self, area1=2.0, area2=2.5, outflow_coeff1=0.5, outflow_coeff2=0.4,
                 initial_level1=2.0, initial_level2=2.0):
        self.area1 = area1
        self.area2 = area2
        self.c1 = outflow_coeff1  # governs gravity flow FROM tank1 INTO tank2
        self.c2 = outflow_coeff2  # governs tank2's outflow downstream/to atmosphere
        self.h1 = initial_level1
        self.h2 = initial_level2
        self.d1 = 0.0  # unmeasured leak/disturbance on tank1
        self.d2 = 0.0  # unmeasured leak/disturbance on tank2

    def step(self, u1, u2, dt=0.1):
        flow_1_to_2 = self.c1 * np.sqrt(max(self.h1, 0.0))
        outflow_2 = self.c2 * np.sqrt(max(self.h2, 0.0))

        dh1dt = (u1 - flow_1_to_2 - self.d1) / self.area1
        dh2dt = (u2 + flow_1_to_2 - outflow_2 - self.d2) / self.area2

        # float() keeps np.sqrt's np.float64 from leaking into telemetry/candidate
        # code - see tank_sim.LeakyTank.step for the JSON-serialization bug this avoids.
        self.h1 = float(max(0.0, self.h1 + dh1dt * dt))
        self.h2 = float(max(0.0, self.h2 + dh2dt * dt))
        return self.h1, self.h2


@dataclass
class TwoTankScenarioConfig:
    name: str
    sim_time: float = 30.0
    dt: float = 0.1
    nominal_setpoint1: float = 3.0
    nominal_setpoint2: float = 3.0
    leak1_onset_s: Optional[float] = None
    leak1_magnitude: float = 0.0
    leak1_offset_s: Optional[float] = None
    leak2_onset_s: Optional[float] = None
    leak2_magnitude: float = 0.0
    leak2_offset_s: Optional[float] = None
    sensor_noise_std: float = 0.0
    initial_level1: float = 2.0
    initial_level2: float = 2.0
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
    """Compact numeric summary for embedding in an LLM prompt, mirroring
    tank_sim.build_log_report for the two-tank case."""
    return {
        "scenario": episode_result["scenario"],
        "metrics": episode_result["metrics"],
        "failure_point_counts": _count_failure_points(episode_result["failure_points"]),
    }


def _count_failure_points(failure_points: list) -> dict:
    counts = {}
    for fp in failure_points:
        key = f"{fp['tank']}:{fp['type']}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def run_episode(supervisor_fn: Callable, scenario: TwoTankScenarioConfig) -> dict:
    if scenario.seed is not None:
        np.random.seed(scenario.seed)

    tank = TwoTankSystem(initial_level1=scenario.initial_level1, initial_level2=scenario.initial_level2)
    pid1 = PIDController(Kp=scenario.pid_kp, Ki=scenario.pid_ki, Kd=scenario.pid_kd,
                          setpoint=scenario.nominal_setpoint1, output_limits=scenario.pid_output_limits)
    pid2 = PIDController(Kp=scenario.pid_kp, Ki=scenario.pid_ki, Kd=scenario.pid_kd,
                          setpoint=scenario.nominal_setpoint2, output_limits=scenario.pid_output_limits)

    active_setpoints = {"tank1": scenario.nominal_setpoint1, "tank2": scenario.nominal_setpoint2}
    simulation_steps = int(scenario.sim_time / scenario.dt)

    time_hist = []
    level1_hist, level2_hist = [], []
    setpoint1_hist, setpoint2_hist = [], []
    pump1_hist, pump2_hist = [], []
    leak1_hist, leak2_hist = [], []
    telemetry_buffer = []
    supervisor_decisions = []
    failure_points = []

    iae_accum = 0.0
    last_anomaly_flags = {"tank1": False, "tank2": False}

    for t_step in range(simulation_steps):
        t = round(t_step * scenario.dt, 3)

        if scenario.leak1_onset_s is not None and t == scenario.leak1_onset_s:
            tank.d1 = scenario.leak1_magnitude
        if scenario.leak1_offset_s is not None and t == scenario.leak1_offset_s:
            tank.d1 = 0.0
        if scenario.leak2_onset_s is not None and t == scenario.leak2_onset_s:
            tank.d2 = scenario.leak2_magnitude
        if scenario.leak2_offset_s is not None and t == scenario.leak2_offset_s:
            tank.d2 = 0.0

        fault_active = {"tank1": tank.d1 != 0.0, "tank2": tank.d2 != 0.0}

        pid1.setpoint = active_setpoints["tank1"]
        pid2.setpoint = active_setpoints["tank2"]

        m1, m2 = tank.h1, tank.h2
        if scenario.sensor_noise_std > 0.0:
            m1 = float(m1 + np.random.normal(0.0, scenario.sensor_noise_std))
            m2 = float(m2 + np.random.normal(0.0, scenario.sensor_noise_std))

        u1 = float(pid1.update(measurement=m1, current_time=t))
        u2 = float(pid2.update(measurement=m2, current_time=t))
        h1, h2 = tank.step(u1, u2, scenario.dt)

        error1 = active_setpoints["tank1"] - h1
        error2 = active_setpoints["tank2"] - h2
        iae_accum += (abs(error1) + abs(error2)) * scenario.dt

        time_hist.append(t)
        level1_hist.append(h1)
        level2_hist.append(h2)
        setpoint1_hist.append(active_setpoints["tank1"])
        setpoint2_hist.append(active_setpoints["tank2"])
        pump1_hist.append(u1)
        pump2_hist.append(u2)
        leak1_hist.append(tank.d1)
        leak2_hist.append(tank.d2)

        telemetry_buffer.append({
            "time": t,
            "tank1": {"level": round(h1, 2), "pump_effort": round(u1, 2), "error": round(error1, 2)},
            "tank2": {"level": round(h2, 2), "pump_effort": round(u2, 2), "error": round(error2, 2)},
        })

        for tank_name, level in (("tank1", h1), ("tank2", h2)):
            if level < scenario.safety_bounds[0] or level > scenario.safety_bounds[1]:
                failure_points.append({"time_s": t, "type": "safety_violation", "tank": tank_name, "detail": f"level={level:.3f}"})

        for tank_name in ("tank1", "tank2"):
            if fault_active[tank_name] and not last_anomaly_flags[tank_name]:
                failure_points.append({"time_s": t, "type": "missed_anomaly", "tank": tank_name, "detail": "fault active, last anomaly_flag=False"})
            elif not fault_active[tank_name] and last_anomaly_flags[tank_name]:
                failure_points.append({"time_s": t, "type": "false_positive", "tank": tank_name, "detail": "no fault, last anomaly_flag=True"})

        if t_step > 0 and t_step % scenario.macro_cycle_steps == 0:
            window = telemetry_buffer[-scenario.macro_cycle_steps:]
            nominal_targets = {"tank1": scenario.nominal_setpoint1, "tank2": scenario.nominal_setpoint2}
            (decision, err) = call_with_timeout(
                supervisor_fn, (window, dict(active_setpoints), nominal_targets),
                timeout_s=scenario.supervisor_timeout_s,
            )
            if err is not None:
                fp_type = "timeout" if err == "timeout" else "exception"
                failure_points.append({"time_s": t, "type": fp_type, "tank": "both", "detail": err})
                decision = {
                    "diagnosis": f"SUPERVISOR_FAILURE: {err}. Preserving setpoints.",
                    "adjusted_setpoints": dict(active_setpoints),
                    "anomaly_flags": {"tank1": True, "tank2": True},
                }

            adjusted = decision.get("adjusted_setpoints", {}) or {}
            flags = decision.get("anomaly_flags", {}) or {}

            new_setpoints = {}
            new_flags = {}
            for tank_name in ("tank1", "tank2"):
                proposed = float(adjusted.get(tank_name, active_setpoints[tank_name]))
                new_setpoints[tank_name] = max(scenario.setpoint_clamp[0], min(scenario.setpoint_clamp[1], proposed))
                new_flags[tank_name] = bool(flags.get(tank_name, False))

            supervisor_decisions.append({
                "time_s": t,
                "tank1_level_m": round(tank.h1, 3),
                "tank2_level_m": round(tank.h2, 3),
                "adjusted_setpoints": new_setpoints,
                "anomaly_flags": new_flags,
                "diagnosis": str(decision.get("diagnosis", "")),
            })

            active_setpoints = new_setpoints
            last_anomaly_flags = new_flags

    violation_count = sum(1 for f in failure_points if f["type"] == "safety_violation")
    missed_anomaly_count = sum(1 for f in failure_points if f["type"] == "missed_anomaly")
    false_positive_count = sum(1 for f in failure_points if f["type"] == "false_positive")
    exception_count = sum(1 for f in failure_points if f["type"] in ("exception", "timeout"))

    fault1_active_at_end = bool(leak1_hist and leak1_hist[-1] != 0.0)
    fault2_active_at_end = bool(leak2_hist and leak2_hist[-1] != 0.0)
    restore_gap = 0.0
    if not fault1_active_at_end:
        restore_gap += abs(active_setpoints["tank1"] - scenario.nominal_setpoint1)
    if not fault2_active_at_end:
        restore_gap += abs(active_setpoints["tank2"] - scenario.nominal_setpoint2)

    return {
        "scenario": scenario.name,
        "time_hist": time_hist,
        "level1_hist": level1_hist,
        "level2_hist": level2_hist,
        "setpoint1_hist": setpoint1_hist,
        "setpoint2_hist": setpoint2_hist,
        "pump1_hist": pump1_hist,
        "pump2_hist": pump2_hist,
        "leak1_hist": leak1_hist,
        "leak2_hist": leak2_hist,
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
