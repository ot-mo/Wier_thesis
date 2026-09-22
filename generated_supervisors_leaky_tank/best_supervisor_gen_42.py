def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    efforts = []
    errors = []
    for step in telemetry_window:
        efforts.append(float(step.get("pump_effort", 0.0)))
        errors.append(float(step.get("error", 0.0)))

    # Expected no-leak effort based on active setpoint and nominal target.
    # Baseline no-fault effort is ~0.923 at nominal; 0.9 is a conservative coefficient.
    if nominal_target > 0.0:
        level_ratio = active_setpoint / nominal_target
        level_ratio = max(0.1, min(1.5, level_ratio))
        expected_effort = 0.9 * math.sqrt(level_ratio)
    else:
        expected_effort = 0.9

    # Short window for fast detection; long window for evidence accumulation.
    short_n = min(3, n)
    long_n = min(10, n)
    recent_efforts_short = efforts[-short_n:]
    recent_errors_short = errors[-short_n:]
    recent_efforts_long = efforts[-long_n:]
    recent_errors_long = errors[-long_n:]

    def median(vals):
        if not vals:
            return 0.0
        sv = sorted(vals)
        m = len(sv)
        if m % 2 == 1:
            return sv[m // 2]
        else:
            return (sv[m // 2 - 1] + sv[m // 2]) / 2.0

    med_effort_short = median(recent_efforts_short)
    med_effort_long = median(recent_efforts_long)
    med_error_short = median(recent_errors_short)
    med_error_long = median(recent_errors_long)

    dev_effort_short = [e - expected_effort for e in recent_efforts_short]
    dev_effort_long = [e - expected_effort for e in recent_efforts_long]
    med_dev_short = median(dev_effort_short)
    med_dev_long = median(dev_effort_long)

    # Thresholds for detection
    entry_threshold = 0.18
    exit_threshold = 0.08
    severe_threshold = 0.45
    neg_error_threshold = -0.08

    short_exceed = sum(1 for d in dev_effort_short if d > entry_threshold)
    long_exceed = sum(1 for d in dev_effort_long if d > entry_threshold)

    leak_short = (short_exceed >= 2 and med_dev_short > entry_threshold)
    leak_long = (long_n >= 5 and long_exceed >= 5 and med_dev_long > entry_threshold)
    severe = (med_dev_short > severe_threshold and med_error_short < neg_error_threshold)

    # Clearing condition: if recent effort is near expected and error not significantly negative,
    # we ignore older long-window evidence to prevent false positives after leak ends.
    clear_short = (med_dev_short < exit_threshold and short_exceed <= 1 and med_error_short > -0.03)

    if clear_short:
        anomaly_flag = False
    else:
        anomaly_flag = leak_short or leak_long or severe

    # Adjust setpoint based on severity and restore toward nominal when stable.
    if anomaly_flag:
        if med_dev_short > 0.6:
            adjustment = 0.2
        elif med_dev_short > 0.3:
            adjustment = 0.15
        else:
            adjustment = 0.1
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = (f"Anomaly detected: effort median {med_effort_short:.2f} "
                     f"(excess {med_dev_short:.2f}), lowering setpoint by {adjustment:.2f}.")
    else:
        if (active_setpoint < nominal_target and
            med_dev_short < exit_threshold and
            abs(med_error_short) < 0.04):
            restore_step = min(0.1, nominal_target - active_setpoint)
            adjusted_setpoint = min(nominal_target, active_setpoint + restore_step)
            diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
        else:
            adjusted_setpoint = active_setpoint
            diagnosis = "Nominal operation, no action."

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }