def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    efforts = []
    errors = []
    for step in telemetry_window:
        try:
            efforts.append(float(step.get("pump_effort", 0.0)))
            errors.append(float(step.get("error", 0.0)))
        except Exception:
            efforts.append(0.0)
            errors.append(0.0)

    abs_errors = [abs(e) for e in errors]

    # Use a moderate window for stable statistics
    window_n = min(12, n)
    recent_efforts = efforts[-window_n:]
    recent_abs_errors = abs_errors[-window_n:]

    # Median effort
    sorted_efforts = sorted(recent_efforts)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0

    # Median absolute error
    sorted_errors = sorted(recent_abs_errors)
    me = len(sorted_errors)
    if me % 2 == 1:
        median_abs_error = sorted_errors[me // 2]
    else:
        median_abs_error = (sorted_errors[me // 2 - 1] + sorted_errors[me // 2]) / 2.0

    # Adaptive thresholds: scale with observed baseline, with floors to avoid zero
    effort_threshold = max(1.2, median_effort * 1.35)
    error_threshold = max(0.05, median_abs_error * 1.8)

    # Short confirmation window (last 5 steps) with majority vote
    confirm_n = min(5, window_n)
    confirm_efforts = recent_efforts[-confirm_n:]
    confirm_errors = recent_abs_errors[-confirm_n:]

    effort_exceed = sum(1 for e in confirm_efforts if e > effort_threshold)
    error_exceed = sum(1 for e in confirm_errors if e > error_threshold)

    effort_persistent = effort_exceed >= max(1, int(0.6 * confirm_n))
    error_persistent = error_exceed >= max(1, int(0.6 * confirm_n))

    # Trend detector: compare recent half vs earlier half of the window
    trend_flag = False
    if window_n >= 6:
        half = window_n // 2
        early_efforts = recent_efforts[:half]
        late_efforts = recent_efforts[half:]
        early_errors = recent_abs_errors[:half]
        late_errors = recent_abs_errors[half:]

        early_effort_avg = sum(early_efforts) / len(early_efforts)
        late_effort_avg = sum(late_efforts) / len(late_efforts)
        early_error_avg = sum(early_errors) / len(early_errors)
        late_error_avg = sum(late_errors) / len(late_errors)

        effort_rise = late_effort_avg - early_effort_avg
        error_rise = late_error_avg - early_error_avg

        # Flag if effort or error is rising significantly and late values are above adaptive thresholds
        if effort_rise > 0.4 and late_effort_avg > effort_threshold:
            trend_flag = True
        if error_rise > 0.04 and late_error_avg > error_threshold:
            trend_flag = True

    anomaly_flag = effort_persistent or error_persistent or trend_flag

    # Setpoint adjustment
    if anomaly_flag:
        # Reduce setpoint proportionally to severity, but not below 0
        severity = 0.0
        if effort_threshold > 0:
            severity = max(severity, (median_effort - effort_threshold) / max(effort_threshold, 0.1))
        if error_threshold > 0:
            severity = max(severity, (median_abs_error - error_threshold) / max(error_threshold, 0.1))
        adjustment = min(0.3, 0.1 + 0.1 * severity)
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {median_effort:.2f} (thr {effort_threshold:.2f}), error {median_abs_error:.2f} (thr {error_threshold:.2f}), lowering setpoint by {adjustment:.2f}."
    elif median_effort < 1.1 and median_abs_error < 0.03 and active_setpoint < nominal_target:
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