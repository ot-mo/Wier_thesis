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

    # Use a window of up to 20 steps for robust statistics
    window_size = min(20, n)
    recent_efforts = efforts[-window_size:]
    recent_abs_errors = abs_errors[-window_size:]

    # Compute median and MAD for effort and error
    def median(lst):
        s = sorted(lst)
        m = len(s)
        if m == 0:
            return 0.0
        if m % 2 == 1:
            return s[m // 2]
        return (s[m // 2 - 1] + s[m // 2]) / 2.0

    def mad(lst, med):
        if not lst:
            return 0.0
        deviations = [abs(x - med) for x in lst]
        return median(deviations)

    med_effort = median(recent_efforts)
    med_abs_error = median(recent_abs_errors)
    mad_effort = mad(recent_efforts, med_effort)
    mad_error = mad(recent_abs_errors, med_abs_error)

    # Adaptive thresholds: median + 3 * MAD (robust to outliers)
    effort_threshold = med_effort + 3.0 * mad_effort
    error_threshold = med_abs_error + 3.0 * mad_error

    # Ensure thresholds are not too low (avoid false positives from noise)
    effort_threshold = max(effort_threshold, 1.2)
    error_threshold = max(error_threshold, 0.05)

    # Count exceedances in the recent window
    effort_exceed = sum(1 for e in recent_efforts if e > effort_threshold)
    error_exceed = sum(1 for e in recent_abs_errors if e > error_threshold)

    # Require a minimum number of exceedances to flag anomaly (persistence)
    required = max(2, int(0.5 * window_size))
    effort_persistent = effort_exceed >= required
    error_persistent = error_exceed >= required

    # Also check for sustained effort increase without error decrease
    # Compute trend: compare first half vs second half of window
    half = window_size // 2
    if half > 0:
        first_half_effort = sum(recent_efforts[:half]) / half
        second_half_effort = sum(recent_efforts[half:]) / (window_size - half)
        first_half_error = sum(recent_abs_errors[:half]) / half
        second_half_error = sum(recent_abs_errors[half:]) / (window_size - half)
        effort_increasing = second_half_effort > first_half_effort * 1.2
        error_not_decreasing = second_half_error > first_half_error * 0.9
    else:
        effort_increasing = False
        error_not_decreasing = False

    anomaly_flag = (effort_persistent and error_persistent) or (effort_increasing and error_not_decreasing)

    # Estimate leak severity: excess effort above median
    excess_effort = max(0.0, med_effort - 1.0)
    if anomaly_flag:
        # Adjust setpoint downward proportionally to excess effort, but cap at 0.5
        adjustment = min(0.5, 0.1 + 0.2 * excess_effort)
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: median effort {med_effort:.2f}, median error {med_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif med_effort < 1.1 and med_abs_error < 0.03 and active_setpoint < nominal_target:
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