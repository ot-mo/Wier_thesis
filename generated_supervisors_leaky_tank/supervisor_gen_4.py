def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    efforts = []
    errors = []
    for step in telemetry_window:
        efforts.append(step.get("pump_effort", 0.0))
        errors.append(step.get("error", 0.0))
    abs_errors = [abs(e) for e in errors]

    # Robust baseline: median of all efforts (or first 20 if many samples)
    baseline_n = min(20, n)
    baseline_effort = sorted(efforts[:baseline_n])[baseline_n // 2]

    # Recent effort: average of last 5 steps (or all if fewer)
    recent_n = min(5, n)
    recent_efforts = efforts[-recent_n:]
    avg_recent_effort = sum(recent_efforts) / len(recent_efforts)

    # Recent error: average absolute error of last 5 steps
    recent_abs_errors = abs_errors[-recent_n:]
    avg_recent_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    # Variability of recent efforts (median absolute deviation)
    if len(recent_efforts) > 1:
        med = sorted(recent_efforts)[len(recent_efforts) // 2]
        mad = sum(abs(e - med) for e in recent_efforts) / len(recent_efforts)
    else:
        mad = 0.0

    # Dynamic threshold: baseline plus a margin based on variability and a small constant
    # Use a margin that is at least 0.2 and scales with MAD
    margin = max(0.2, 2.0 * mad)
    effort_threshold = baseline_effort + margin

    # Anomaly detection:
    # 1. Recent average effort exceeds threshold
    # 2. OR at least 3 of last 5 samples exceed threshold (sustained)
    # 3. OR very high effort relative to baseline (e.g., > 2x baseline + 0.5)
    above_count = sum(1 for e in recent_efforts if e > effort_threshold)
    sustained_high = above_count >= 3
    very_high_effort = avg_recent_effort > (2.0 * baseline_effort + 0.5)
    high_error = avg_recent_abs_error > 0.15

    anomaly_flag = (avg_recent_effort > effort_threshold) or sustained_high or (very_high_effort and high_error)

    # Setpoint adjustment
    if anomaly_flag:
        # Lower setpoint proportionally to effort excess, with larger cap for severe leaks
        excess = max(0.0, avg_recent_effort - baseline_effort)
        if avg_recent_effort > 3.0:
            adjustment = min(1.0, max(0.3, excess * 0.6))
        else:
            adjustment = min(0.8, max(0.2, excess * 0.4))
        adjusted_setpoint = active_setpoint - adjustment
        diagnosis = f"Anomaly detected: effort {avg_recent_effort:.2f} vs baseline {baseline_effort:.2f}, lowering setpoint by {adjustment:.2f}."
    elif avg_recent_effort < baseline_effort * 1.1 and avg_recent_abs_error < 0.15 and active_setpoint < nominal_target:
        # Stable and below nominal: restore more aggressively
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.5)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }