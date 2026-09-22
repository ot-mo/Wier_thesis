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

    # Use a longer window for more stable statistics
    window_n = min(10, n)
    recent_efforts = efforts[-window_n:]
    recent_abs_errors = abs_errors[-window_n:]

    # Compute robust statistics
    sorted_efforts = sorted(recent_efforts)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0

    sorted_errors = sorted(recent_abs_errors)
    if m % 2 == 1:
        median_abs_error = sorted_errors[m // 2]
    else:
        median_abs_error = (sorted_errors[m // 2 - 1] + sorted_errors[m // 2]) / 2.0

    # Compute MAD (median absolute deviation) for effort and error
    effort_devs = [abs(e - median_effort) for e in recent_efforts]
    sorted_effort_devs = sorted(effort_devs)
    if m % 2 == 1:
        mad_effort = sorted_effort_devs[m // 2]
    else:
        mad_effort = (sorted_effort_devs[m // 2 - 1] + sorted_effort_devs[m // 2]) / 2.0

    error_devs = [abs(e - median_abs_error) for e in recent_abs_errors]
    sorted_error_devs = sorted(error_devs)
    if m % 2 == 1:
        mad_error = sorted_error_devs[m // 2]
    else:
        mad_error = (sorted_error_devs[m // 2 - 1] + sorted_error_devs[m // 2]) / 2.0

    # Adaptive thresholds: median + k * MAD, with minimum floors
    k = 3.0
    effort_threshold = max(1.2, median_effort + k * mad_effort)
    error_threshold = max(0.05, median_abs_error + k * mad_error)

    # Count exceedances in the window
    effort_exceed = sum(1 for e in recent_efforts if e > effort_threshold)
    error_exceed = sum(1 for e in recent_abs_errors if e > error_threshold)

    # Require a higher fraction for anomaly (e.g., 70% of window)
    required = max(1, int(0.7 * window_n))
    effort_persistent = effort_exceed >= required
    error_persistent = error_exceed >= required

    # Hysteresis: only set anomaly if not already set, or if very strong evidence
    # Since we don't have state, we use a simple rule: anomaly if both effort and error are high, or one is extremely high
    # But to avoid false positives, we require both effort and error to be persistent, or one with a very high count
    anomaly_flag = (effort_persistent and error_persistent) or (effort_exceed >= window_n - 1) or (error_exceed >= window_n - 1)

    # Additional check: if median effort is very high and error is high, flag anomaly
    if not anomaly_flag and median_effort > 2.0 and median_abs_error > 0.1:
        anomaly_flag = True

    # Compute average absolute error for diagnosis
    avg_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    if anomaly_flag:
        # Adjust setpoint more conservatively: reduce by 0.05, but not below 0
        adjustment = 0.05
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {median_effort:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_effort < 1.1 and avg_abs_error < 0.03 and active_setpoint < nominal_target:
        # Restore setpoint gradually
        adjusted_setpoint = min(nominal_target, active_setpoint + 0.05)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }