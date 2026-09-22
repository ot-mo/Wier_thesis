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

    # Short window for rapid detection
    short_n = min(3, n)
    short_efforts = efforts[-short_n:]
    short_abs_errors = abs_errors[-short_n:]

    # Long window for persistent detection
    long_n = min(10, n)
    long_efforts = efforts[-long_n:]
    long_abs_errors = abs_errors[-long_n:]

    # Helper to compute median
    def median(lst):
        s = sorted(lst)
        m = len(s)
        if m == 0:
            return 0.0
        if m % 2 == 1:
            return s[m // 2]
        else:
            return (s[m // 2 - 1] + s[m // 2]) / 2.0

    # Helper to compute MAD
    def mad(lst):
        med = median(lst)
        abs_devs = [abs(x - med) for x in lst]
        return median(abs_devs)

    # Adaptive thresholds based on recent statistics
    # For effort: use median + 3*MAD, but at least a minimum based on nominal_target
    med_effort_short = median(short_efforts)
    mad_effort_short = mad(short_efforts)
    effort_threshold_short = max(1.5 * nominal_target, med_effort_short + 3 * mad_effort_short)

    med_effort_long = median(long_efforts)
    mad_effort_long = mad(long_efforts)
    effort_threshold_long = max(1.2 * nominal_target, med_effort_long + 3 * mad_effort_long)

    # For error: use median + 3*MAD, but at least a minimum based on nominal_target
    med_error_long = median(long_abs_errors)
    mad_error_long = mad(long_abs_errors)
    error_threshold_long = max(0.1 * nominal_target, med_error_long + 3 * mad_error_long)

    # Detection logic
    # Severe anomaly: short-term effort exceeds high threshold
    severe_anomaly = med_effort_short > effort_threshold_short

    # Mild/persistent anomaly: long-term effort exceeds lower threshold AND long-term error exceeds threshold
    persistent_anomaly = (med_effort_long > effort_threshold_long) and (med_error_long > error_threshold_long)

    anomaly_flag = severe_anomaly or persistent_anomaly

    # Compute median effort and average error for diagnosis
    median_effort = med_effort_long
    avg_abs_error = sum(long_abs_errors) / len(long_abs_errors) if long_abs_errors else 0.0

    if anomaly_flag:
        # Larger adjustment to quickly mitigate leak
        adjustment = 0.2
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {median_effort:.2f}, error {avg_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    elif median_effort < 1.1 and avg_abs_error < 0.03 and active_setpoint < nominal_target:
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