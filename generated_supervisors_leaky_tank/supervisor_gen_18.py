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

    # Use a longer window for robust statistics
    window_n = min(10, n)
    recent_efforts = efforts[-window_n:]
    recent_abs_errors = abs_errors[-window_n:]

    # Compute median and MAD for effort and error
    def median(lst):
        s = sorted(lst)
        m = len(s)
        if m == 0:
            return 0.0
        if m % 2 == 1:
            return s[m // 2]
        return (s[m // 2 - 1] + s[m // 2]) / 2.0

    med_effort = median(recent_efforts)
    med_error = median(recent_abs_errors)

    # MAD
    mad_effort = median([abs(e - med_effort) for e in recent_efforts])
    mad_error = median([abs(e - med_error) for e in recent_abs_errors])

    # Adaptive thresholds: median + k * MAD, with minimum floors
    k = 3.0
    effort_threshold = max(1.0, med_effort + k * mad_effort)
    error_threshold = max(0.02, med_error + k * mad_error)

    # Count exceedances in recent window
    effort_exceed = sum(1 for e in recent_efforts if e > effort_threshold)
    error_exceed = sum(1 for e in recent_abs_errors if e > error_threshold)

    # Require at least 2 exceedances in the window (or 20%)
    required = max(1, int(0.2 * window_n))
    effort_persistent = effort_exceed >= required
    error_persistent = error_exceed >= required

    # Also check for sustained high effort relative to nominal
    avg_effort = sum(recent_efforts) / len(recent_efforts)
    avg_error = sum(recent_abs_errors) / len(recent_abs_errors)
    high_effort = avg_effort > max(1.5, 0.9 * nominal_target)
    high_error = avg_error > max(0.05, 0.1 * nominal_target)

    anomaly_flag = effort_persistent or error_persistent or high_effort or high_error

    # Compute severity for adjustment
    severity = 0.0
    if anomaly_flag:
        # Severity based on how much effort/error exceeds thresholds
        effort_sev = max(0.0, (avg_effort - effort_threshold) / max(1.0, effort_threshold))
        error_sev = max(0.0, (avg_error - error_threshold) / max(0.01, error_threshold))
        severity = max(effort_sev, error_sev)
        severity = min(1.0, severity)

    if anomaly_flag:
        # Proportional adjustment: lower setpoint by up to 0.5, at least 0.05
        adjustment = max(0.05, min(0.5, 0.5 * severity))
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {avg_effort:.2f}, error {avg_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif med_effort < 1.1 and med_error < 0.03 and active_setpoint < nominal_target:
        # Restore setpoint gradually, but only if stable for several steps
        # Check last 3 steps for stability
        stable = True
        if n >= 3:
            last_efforts = efforts[-3:]
            last_errors = abs_errors[-3:]
            if max(last_efforts) - min(last_efforts) > 0.2 or max(last_errors) - min(last_errors) > 0.02:
                stable = False
        if stable:
            adjusted_setpoint = min(nominal_target, active_setpoint + 0.1)
            diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
        else:
            adjusted_setpoint = active_setpoint
            diagnosis = "System stable but fluctuating, holding setpoint."
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }