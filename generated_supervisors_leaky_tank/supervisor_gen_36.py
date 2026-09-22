def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    # Extract efforts and errors
    efforts = []
    errors = []
    for step in telemetry_window:
        efforts.append(step.get("pump_effort", 0.0))
        errors.append(step.get("error", 0.0))
    abs_errors = [abs(e) for e in errors]

    # Use up to last 20 steps for analysis
    window_size = min(20, n)
    recent_efforts = efforts[-window_size:]
    recent_abs_errors = abs_errors[-window_size:]

    # Compute weighted moving average with exponential weights (more recent = higher weight)
    # Weight decay factor: 0.9
    weights = [0.9 ** (window_size - 1 - i) for i in range(window_size)]
    total_weight = sum(weights)
    weighted_effort = sum(w * e for w, e in zip(weights, recent_efforts)) / total_weight
    weighted_abs_error = sum(w * e for w, e in zip(weights, recent_abs_errors)) / total_weight

    # Compute median and MAD for robust baseline
    sorted_efforts = sorted(recent_efforts)
    m = len(sorted_efforts)
    if m % 2 == 1:
        median_effort = sorted_efforts[m // 2]
    else:
        median_effort = (sorted_efforts[m // 2 - 1] + sorted_efforts[m // 2]) / 2.0

    # MAD for effort
    abs_devs = [abs(e - median_effort) for e in recent_efforts]
    sorted_devs = sorted(abs_devs)
    if m % 2 == 1:
        mad_effort = sorted_devs[m // 2]
    else:
        mad_effort = (sorted_devs[m // 2 - 1] + sorted_devs[m // 2]) / 2.0

    # Adaptive thresholds: median + 3*MAD, but at least a minimum based on nominal_target
    effort_threshold = max(median_effort + 3.0 * mad_effort, 0.8 * nominal_target)
    # For error, use a fixed fraction of nominal_target plus a small absolute floor
    error_threshold = max(0.05 * nominal_target, 0.02)

    # Anomaly detection: weighted effort exceeds threshold OR weighted error exceeds threshold
    # Also require that the recent trend is not just a single spike: check that at least 2 of last 3 steps exceed threshold
    recent_efforts_short = recent_efforts[-3:] if len(recent_efforts) >= 3 else recent_efforts
    recent_errors_short = recent_abs_errors[-3:] if len(recent_abs_errors) >= 3 else recent_abs_errors
    effort_exceed_short = sum(1 for e in recent_efforts_short if e > effort_threshold)
    error_exceed_short = sum(1 for e in recent_errors_short if e > error_threshold)
    required_short = max(1, int(0.6 * len(recent_efforts_short)))

    effort_anomaly = (weighted_effort > effort_threshold) and (effort_exceed_short >= required_short)
    error_anomaly = (weighted_abs_error > error_threshold) and (error_exceed_short >= required_short)

    anomaly_flag = effort_anomaly or error_anomaly

    # Determine adjustment magnitude based on severity
    if anomaly_flag:
        # Proportional adjustment: how much effort exceeds threshold
        excess_ratio = max(0.0, (weighted_effort - effort_threshold) / max(effort_threshold, 0.1))
        # Base adjustment 0.1, scaled by excess_ratio, capped at 0.5
        adjustment = min(0.5, 0.1 + 0.2 * excess_ratio)
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: weighted effort {weighted_effort:.2f}, error {weighted_abs_error:.2f}, lowering setpoint by {adjustment:.2f}."
    else:
        # Restoration logic: if system is stable and below nominal, increase setpoint
        # Relaxed conditions: weighted effort < 1.2 * nominal_target and weighted error < 0.05 * nominal_target
        if (weighted_effort < 1.2 * nominal_target and weighted_abs_error < 0.05 * nominal_target and active_setpoint < nominal_target):
            # Increase setpoint by 0.1, but not above nominal_target
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