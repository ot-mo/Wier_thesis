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

    # Robust baseline: median of first up to 10 samples
    baseline_n = min(10, n)
    baseline_effort = sorted(efforts[:baseline_n])[baseline_n // 2]

    # Recent effort: average of last 3 steps (or all if fewer)
    recent_efforts = efforts[-3:] if n >= 3 else efforts
    avg_recent_effort = sum(recent_efforts) / len(recent_efforts)

    # Recent error: average absolute error of last 3 steps
    recent_abs_errors = abs_errors[-3:] if n >= 3 else abs_errors
    avg_recent_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    # Dynamic threshold based on baseline and recent variability
    # Use a small margin above baseline to catch leaks early
    effort_threshold = baseline_effort * 1.15 + 0.2

    # Count consecutive recent samples above threshold (within last 5)
    lookback = min(5, n)
    above_count = 0
    for e in efforts[-lookback:]:
        if e > effort_threshold:
            above_count += 1

    # Anomaly if sustained high effort (at least 2 of last 5 above threshold)
    # OR very high absolute effort with any error
    sustained_high = above_count >= 2
    very_high_effort = avg_recent_effort > 2.5
    high_error = avg_recent_abs_error > 0.15

    anomaly_flag = sustained_high or (very_high_effort and high_error)

    # Setpoint adjustment
    if anomaly_flag:
        # Lower setpoint proportionally to effort excess, with larger cap for severe leaks
        excess = max(0.0, avg_recent_effort - baseline_effort)
        if avg_recent_effort > 3.0:
            adjustment = min(1.0, max(0.2, excess * 0.5))
        else:
            adjustment = min(0.6, max(0.1, excess * 0.3))
        adjusted_setpoint = active_setpoint - adjustment
        diagnosis = f"Anomaly detected: effort {avg_recent_effort:.2f} vs baseline {baseline_effort:.2f}, lowering setpoint by {adjustment:.2f}."
    elif avg_recent_effort < baseline_effort * 1.1 and avg_recent_abs_error < 0.15 and active_setpoint < nominal_target:
        # Stable and below nominal: restore more aggressively
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.4)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }