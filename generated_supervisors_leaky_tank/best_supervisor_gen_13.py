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

    # Use a longer window for stable statistics
    window_n = min(10, n)
    recent_efforts = efforts[-window_n:]
    recent_abs_errors = abs_errors[-window_n:]

    # Median effort
    sorted_efforts = sorted(recent_efforts)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0

    # Average absolute error
    avg_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    # Fixed thresholds based on physical expectations
    EFFORT_THRESHOLD = 1.5
    ERROR_THRESHOLD = 0.1

    # Anomaly detection: single condition with confirmation
    # Use a simple counter to avoid transient false positives
    # Since we don't have state, we check if the condition holds for the entire recent window
    # or use a majority vote
    effort_high = median_effort > EFFORT_THRESHOLD
    error_high = avg_abs_error > ERROR_THRESHOLD

    # Require the condition to be persistent: check if at least 70% of recent steps exceed threshold
    effort_exceed_count = sum(1 for e in recent_efforts if e > EFFORT_THRESHOLD)
    error_exceed_count = sum(1 for e in recent_abs_errors if e > ERROR_THRESHOLD)
    effort_persistent = effort_exceed_count >= 0.7 * window_n
    error_persistent = error_exceed_count >= 0.7 * window_n

    anomaly_flag = effort_persistent or error_persistent

    # Setpoint adjustment
    if anomaly_flag:
        # Reduce setpoint by a small fixed amount, but not below 0
        adjustment = 0.1
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {median_effort:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_effort < 1.1 and avg_abs_error < 0.03 and active_setpoint < nominal_target:
        # Restore gradually
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.1)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }