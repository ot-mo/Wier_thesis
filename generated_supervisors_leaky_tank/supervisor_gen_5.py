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

    # Robust baseline: median of first up to 20 samples
    baseline_n = min(20, n)
    baseline_effort = sorted(efforts[:baseline_n])[baseline_n // 2]

    # Recent effort: median of last 5 steps (or all if fewer)
    recent_efforts = efforts[-5:] if n >= 5 else efforts
    recent_effort = sorted(recent_efforts)[len(recent_efforts) // 2]

    # Recent error: median of absolute errors of last 5 steps
    recent_abs_errors = abs_errors[-5:] if n >= 5 else abs_errors
    recent_abs_error = sorted(recent_abs_errors)[len(recent_abs_errors) // 2]

    # Compute median absolute deviation (MAD) of recent efforts for variability
    lookback_mad = min(10, n)
    recent_for_mad = efforts[-lookback_mad:]
    med = sorted(recent_for_mad)[len(recent_for_mad) // 2]
    abs_devs = [abs(e - med) for e in recent_for_mad]
    mad = sorted(abs_devs)[len(abs_devs) // 2]

    # Dynamic threshold: baseline plus margin based on variability
    margin = max(0.3, 3.0 * mad)
    effort_threshold = baseline_effort + margin

    # Anomaly detection: recent effort exceeds threshold OR (high effort and high error)
    anomaly_flag = (recent_effort > effort_threshold) or (recent_effort > baseline_effort * 1.5 and recent_abs_error > 0.1)

    # Setpoint adjustment
    if anomaly_flag:
        excess = max(0.0, recent_effort - baseline_effort)
        adjustment = min(0.5, max(0.1, excess * 0.2))
        adjusted_setpoint = active_setpoint - adjustment
        diagnosis = f"Anomaly detected: recent effort {recent_effort:.2f} vs baseline {baseline_effort:.2f}, lowering setpoint by {adjustment:.2f}."
    elif recent_effort < baseline_effort * 1.1 and recent_abs_error < 0.1 and active_setpoint < nominal_target:
        # Stable and below nominal: restore gently
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.2)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }