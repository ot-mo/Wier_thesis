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

    # Use a long window for robust statistics
    long_n = min(20, n)
    recent_efforts = efforts[-long_n:]
    recent_abs_errors = abs_errors[-long_n:]

    # Compute median and MAD for effort and error
    def median_and_mad(values):
        if not values:
            return 0.0, 0.0
        sorted_vals = sorted(values)
        m = len(sorted_vals)
        if m % 2 == 1:
            med = sorted_vals[m // 2]
        else:
            med = (sorted_vals[m // 2 - 1] + sorted_vals[m // 2]) / 2.0
        abs_devs = [abs(v - med) for v in values]
        sorted_devs = sorted(abs_devs)
        if m % 2 == 1:
            mad = sorted_devs[m // 2]
        else:
            mad = (sorted_devs[m // 2 - 1] + sorted_devs[m // 2]) / 2.0
        return med, mad

    med_effort, mad_effort = median_and_mad(recent_efforts)
    med_error, mad_error = median_and_mad(recent_abs_errors)

    # Adaptive thresholds: baseline + k * MAD, with minimum floors
    effort_threshold = max(1.2, med_effort + 2.5 * mad_effort)
    error_threshold = max(0.05, med_error + 2.5 * mad_error)

    # Compute leak score: fraction of recent samples exceeding thresholds
    effort_exceed = sum(1 for e in recent_efforts if e > effort_threshold)
    error_exceed = sum(1 for e in recent_abs_errors if e > error_threshold)
    effort_frac = effort_exceed / long_n
    error_frac = error_exceed / long_n

    # Leak score combines effort and error exceedance fractions
    leak_score = max(effort_frac, error_frac)

    # Hysteresis: require higher score to enter anomaly, lower to exit
    # Use a short window for fast detection of severe leaks
    short_n = min(5, n)
    recent_efforts_short = efforts[-short_n:]
    recent_abs_errors_short = abs_errors[-short_n:]
    effort_exceed_short = sum(1 for e in recent_efforts_short if e > effort_threshold)
    error_exceed_short = sum(1 for e in recent_abs_errors_short if e > error_threshold)
    short_frac = max(effort_exceed_short, error_exceed_short) / short_n

    # Anomaly if either short or long window shows significant exceedance
    anomaly_flag = (short_frac >= 0.6) or (leak_score >= 0.5)

    # Compute average absolute error for diagnosis
    avg_abs_error = sum(recent_abs_errors) / long_n if long_n > 0 else 0.0

    if anomaly_flag:
        # Proportional adjustment based on leak severity
        # Scale adjustment between 0.1 and 0.5 depending on leak_score
        adjustment = min(0.5, max(0.1, 0.5 * leak_score))
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort {med_effort:.2f}, error {avg_abs_error:.2f}, leak score {leak_score:.2f}, lowering setpoint by {adjustment:.2f}."
    elif leak_score < 0.2 and avg_abs_error < 0.05 and active_setpoint < nominal_target:
        # Restore setpoint gradually when system is stable and below nominal
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