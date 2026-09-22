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
    recent_efforts_sorted = sorted(recent_efforts)
    m = len(recent_efforts_sorted)
    if m % 2 == 1:
        median_recent_effort = recent_efforts_sorted[m // 2]
    else:
        median_recent_effort = (recent_efforts_sorted[m // 2 - 1] + recent_efforts_sorted[m // 2]) / 2.0

    # Recent error: average absolute error of last 5 steps
    recent_abs_errors = abs_errors[-5:] if n >= 5 else abs_errors
    avg_recent_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    # Dynamic threshold based on baseline and recent variability (MAD)
    if m > 1:
        deviations = [abs(e - median_recent_effort) for e in recent_efforts]
        mad = sorted(deviations)[m // 2]
    else:
        mad = 0.0
    effort_threshold = baseline_effort * 1.1 + 0.1 + 2.0 * mad

    # Anomaly detection: check last 2 consecutive efforts above threshold
    consecutive_high = False
    if n >= 2:
        if efforts[-1] > effort_threshold and efforts[-2] > effort_threshold:
            consecutive_high = True
    elif n == 1:
        if efforts[0] > effort_threshold:
            consecutive_high = True

    # Also flag if recent effort is very high and error is significant
    very_high_effort = median_recent_effort > 2.0
    high_error = avg_recent_abs_error > 0.1
    anomaly_flag = consecutive_high or (very_high_effort and high_error)

    # Setpoint adjustment
    if anomaly_flag:
        excess = max(0.0, median_recent_effort - baseline_effort)
        if median_recent_effort > 3.0:
            adjustment = min(1.0, max(0.3, excess * 0.6))
        else:
            adjustment = min(0.8, max(0.2, excess * 0.4))
        adjusted_setpoint = active_setpoint - adjustment
        diagnosis = f"Anomaly detected: effort {median_recent_effort:.2f} vs baseline {baseline_effort:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_recent_effort < baseline_effort * 1.05 and avg_recent_abs_error < 0.1 and active_setpoint < nominal_target:
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