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

    # Dual windows: short for fast detection, long for confirmation
    short_n = min(3, n)
    long_n = min(10, n)
    short_efforts = efforts[-short_n:]
    short_abs_errors = abs_errors[-short_n:]
    long_efforts = efforts[-long_n:]
    long_abs_errors = abs_errors[-long_n:]

    # Adaptive thresholds using median and MAD
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
        return median([abs(x - med) for x in lst])

    med_effort_long = median(long_efforts)
    mad_effort_long = mad(long_efforts, med_effort_long)
    med_abs_error_long = median(long_abs_errors)
    mad_abs_error_long = mad(long_abs_errors, med_abs_error_long)

    # Base thresholds from nominal target
    base_effort_thresh = max(1.2, 0.8 * nominal_target)
    base_error_thresh = max(0.05, 0.05 * nominal_target)

    # Adaptive thresholds: base + 3*MAD, but not less than base
    effort_thresh = max(base_effort_thresh, med_effort_long + 3 * mad_effort_long)
    error_thresh = max(base_error_thresh, med_abs_error_long + 3 * mad_abs_error_long)

    # Short window detection: any exceedance in short window triggers
    short_effort_exceed = any(e > effort_thresh for e in short_efforts)
    short_error_exceed = any(e > error_thresh for e in short_abs_errors)

    # Long window confirmation: at least 50% exceedance
    long_effort_exceed = sum(1 for e in long_efforts if e > effort_thresh) >= max(1, int(0.5 * long_n))
    long_error_exceed = sum(1 for e in long_abs_errors if e > error_thresh) >= max(1, int(0.5 * long_n))

    # Anomaly if short window shows severe exceedance OR long window confirms
    anomaly_flag = (short_effort_exceed and short_error_exceed) or long_effort_exceed or long_error_exceed

    # Compute median effort and average error for diagnosis
    med_effort = median(short_efforts)
    avg_abs_error = sum(short_abs_errors) / len(short_abs_errors) if short_abs_errors else 0.0

    if anomaly_flag:
        # Proportional adjustment based on error magnitude
        adjustment = min(0.5, max(0.1, 0.2 * avg_abs_error))
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