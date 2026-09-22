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

    # Robust baseline statistics over the entire window
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

    med_effort = median(efforts)
    med_abs_error = median(abs_errors)
    mad_effort = mad(efforts, med_effort)
    mad_abs_error = mad(abs_errors, med_abs_error)

    # Scale factors to avoid division by zero; use small epsilon
    eps = 1e-6
    scale_effort = max(mad_effort, 0.05 * max(1.0, nominal_target), eps)
    scale_error = max(mad_abs_error, 0.02 * max(1.0, nominal_target), eps)

    # Recent window for detection (last 5 steps or all if fewer)
    recent_n = min(5, n)
    recent_efforts = efforts[-recent_n:]
    recent_abs_errors = abs_errors[-recent_n:]

    # Compute robust z-scores for each recent step
    effort_zs = [(e - med_effort) / scale_effort for e in recent_efforts]
    error_zs = [(ae - med_abs_error) / scale_error for ae in recent_abs_errors]

    # Anomaly if any recent step has z-score > threshold (e.g., 3.5)
    # Use max z-score to catch spikes, but require at least 2 steps above threshold for persistence
    threshold = 3.5
    effort_anomaly_count = sum(1 for z in effort_zs if z > threshold)
    error_anomaly_count = sum(1 for z in error_zs if z > threshold)

    # Also consider sustained moderate elevation: mean z-score > 2.0
    mean_effort_z = sum(effort_zs) / len(effort_zs) if effort_zs else 0.0
    mean_error_z = sum(error_zs) / len(error_zs) if error_zs else 0.0

    anomaly_flag = (effort_anomaly_count >= 2 or error_anomaly_count >= 2 or
                    mean_effort_z > 2.0 or mean_error_z > 2.0)

    # Compute severity for adjustment
    severity = max(mean_effort_z, mean_error_z, 0.0)
    if anomaly_flag:
        # Adjust setpoint downward proportionally to severity, bounded
        adjustment = min(0.5, max(0.05, 0.1 * severity))
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f"Anomaly detected: effort z={mean_effort_z:.2f}, error z={mean_error_z:.2f}, lowering setpoint by {adjustment:.2f}."
    elif med_effort < 1.1 and med_abs_error < 0.03 and active_setpoint < nominal_target:
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