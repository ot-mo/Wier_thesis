def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    efforts = []
    errors = []
    for step in telemetry_window:
        try:
            efforts.append(float(step.get("pump_effort", 0.0)))
        except (TypeError, ValueError):
            efforts.append(0.0)
        try:
            errors.append(float(step.get("error", 0.0)))
        except (TypeError, ValueError):
            errors.append(0.0)

    abs_errors = [abs(e) for e in errors]

    # Use a longer window for robust statistics, but not more than available
    window_n = min(10, n)
    recent_efforts = efforts[-window_n:]
    recent_abs_errors = abs_errors[-window_n:]

    # Compute median and MAD for adaptive thresholds
    def median(values):
        s = sorted(values)
        m = len(s)
        if m == 0:
            return 0.0
        if m % 2 == 1:
            return s[m // 2]
        return (s[m // 2 - 1] + s[m // 2]) / 2.0

    med_effort = median(recent_efforts)
    med_abs_error = median(recent_abs_errors)

    # MAD for effort and error
    mad_effort = median([abs(e - med_effort) for e in recent_efforts])
    mad_error = median([abs(e - med_abs_error) for e in recent_abs_errors])

    # Adaptive thresholds: median + 3*MAD, with floors based on nominal target
    effort_threshold = max(1.2, 0.8 * nominal_target, med_effort + 3.0 * mad_effort)
    error_threshold = max(0.05, 0.05 * nominal_target, med_abs_error + 3.0 * mad_error)

    # Count exceedances in recent window
    effort_exceed = sum(1 for e in recent_efforts if e > effort_threshold)
    error_exceed = sum(1 for e in recent_abs_errors if e > error_threshold)

    # Require at least 2 exceedances (or 30% of window) to flag anomaly
    required = max(2, int(0.3 * window_n))
    effort_persistent = effort_exceed >= required
    error_persistent = error_exceed >= required

    # Trend detection: compare first half vs second half of window
    half = window_n // 2
    if half >= 2:
        first_effort = sum(recent_efforts[:half]) / half
        second_effort = sum(recent_efforts[half:]) / (window_n - half)
        first_error = sum(recent_abs_errors[:half]) / half
        second_error = sum(recent_abs_errors[half:]) / (window_n - half)
        effort_trend = second_effort - first_effort
        error_trend = second_error - first_error
        # Flag if effort or error is increasing significantly
        trend_flag = (effort_trend > 0.2 * max(1.0, nominal_target)) or (error_trend > 0.02 * max(1.0, nominal_target))
    else:
        trend_flag = False

    anomaly_flag = effort_persistent or error_persistent or trend_flag

    # Compute severity for adjustment
    avg_effort = sum(recent_efforts) / window_n
    avg_abs_error = sum(recent_abs_errors) / window_n

    if anomaly_flag:
        # Proportional adjustment based on how much effort/error exceed thresholds
        effort_severity = max(0.0, (avg_effort - effort_threshold) / max(1.0, effort_threshold))
        error_severity = max(0.0, (avg_abs_error - error_threshold) / max(0.01, error_threshold))
        severity = max(effort_severity, error_severity)
        # Base adjustment 0.1, scaled up to 0.5 for severe cases
        adjustment = min(0.5, 0.1 + 0.4 * severity)
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {avg_effort:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif avg_effort < 1.1 and avg_abs_error < 0.03 and active_setpoint < nominal_target:
        # Restore setpoint gradually
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