def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    efforts = [step["pump_effort"] for step in telemetry_window]
    errors = [step["error"] for step in telemetry_window]
    abs_errors = [abs(e) for e in errors]

    # Robust baseline: use the minimum of the first few samples (or all if fewer)
    # to avoid contamination from leaks that start early.
    if n >= 5:
        baseline_effort = min(efforts[:5])
    else:
        baseline_effort = min(efforts)

    # Recent effort: average of last 3 steps (or all if fewer)
    recent_efforts = efforts[-3:] if n >= 3 else efforts
    avg_recent_effort = sum(recent_efforts) / len(recent_efforts)

    # Recent error: average absolute error of last 3 steps
    recent_abs_errors = abs_errors[-3:] if n >= 3 else abs_errors
    avg_recent_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    # Dynamic thresholds
    EFFORT_MARGIN = 0.5
    EFFORT_RATIO = 1.3
    ERROR_THRESHOLD = 0.2

    # Leak evidence: effort significantly above baseline
    effort_excess = avg_recent_effort - baseline_effort
    effort_ratio = avg_recent_effort / baseline_effort if baseline_effort > 0.1 else 1.0

    # High effort condition
    high_effort = (effort_excess > EFFORT_MARGIN) or (effort_ratio > EFFORT_RATIO)
    high_error = avg_recent_abs_error > ERROR_THRESHOLD

    # Require two consecutive high-effort steps to reduce false positives
    # Check if the last two steps both have effort above threshold
    if n >= 2:
        last_two_high = all(e > baseline_effort * EFFORT_RATIO or e > baseline_effort + EFFORT_MARGIN for e in efforts[-2:])
    else:
        last_two_high = high_effort

    anomaly_flag = high_effort and (high_error or last_two_high)

    # Setpoint adjustment
    LOWER_STEP = 0.5
    RESTORE_STEP = 0.25

    if anomaly_flag:
        adjustment = min(LOWER_STEP, max(0.1, effort_excess * 0.3))
        adjusted_setpoint = active_setpoint - adjustment
        diagnosis = f"Anomaly detected: effort {avg_recent_effort:.2f} vs baseline {baseline_effort:.2f}, lowering setpoint by {adjustment:.2f}."
    elif avg_recent_effort < baseline_effort * 1.1 and avg_recent_abs_error < 0.15 and active_setpoint < nominal_target:
        adjusted_setpoint = min(nominal_target, active_setpoint + RESTORE_STEP)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }