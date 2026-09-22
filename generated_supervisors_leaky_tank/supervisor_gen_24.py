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

    # Use a longer window for robust detection
    window_n = min(10, n)
    recent_efforts = efforts[-window_n:]
    recent_abs_errors = abs_errors[-window_n:]

    # Compute median and MAD for adaptive thresholds
    def median(lst):
        s = sorted(lst)
        m = len(s)
        if m == 0:
            return 0.0
        if m % 2 == 1:
            return s[m // 2]
        else:
            return (s[m // 2 - 1] + s[m // 2]) / 2.0

    med_effort = median(recent_efforts)
    med_abs_error = median(recent_abs_errors)

    # MAD for effort and error
    mad_effort = median([abs(e - med_effort) for e in recent_efforts])
    mad_error = median([abs(e - med_abs_error) for e in recent_abs_errors])

    # Adaptive thresholds: median + 3*MAD, with minimum floors
    effort_threshold = max(1.2, med_effort + 3.0 * mad_effort)
    error_threshold = max(0.05, med_abs_error + 3.0 * mad_error)

    # Count exceedances in the window
    effort_exceed = sum(1 for e in recent_efforts if e > effort_threshold)
    error_exceed = sum(1 for e in recent_abs_errors if e > error_threshold)

    # Require at least 50% of the window to exceed for anomaly
    required = max(1, int(0.5 * window_n))
    effort_persistent = effort_exceed >= required
    error_persistent = error_exceed >= required

    anomaly_flag = effort_persistent or error_persistent

    # Compute average absolute error for diagnosis
    avg_abs_error = sum(recent_abs_errors) / len(recent_abs_errors)

    if anomaly_flag:
        # Proportional adjustment based on severity
        # Severity: how much effort and error exceed thresholds
        effort_severity = max(0.0, (med_effort - effort_threshold) / max(1e-6, effort_threshold))
        error_severity = max(0.0, (avg_abs_error - error_threshold) / max(1e-6, error_threshold))
        severity = max(effort_severity, error_severity)
        # Adjust setpoint down by up to 0.5, proportional to severity
        adjustment = min(0.5, 0.1 + 0.4 * severity)
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {med_effort:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif med_effort < 1.1 and avg_abs_error < 0.03 and active_setpoint < nominal_target:
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