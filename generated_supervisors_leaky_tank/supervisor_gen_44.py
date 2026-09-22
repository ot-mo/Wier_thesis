def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    efforts = []
    errors = []
    for step in telemetry_window:
        efforts.append(float(step.get("pump_effort", 0.0)))
        errors.append(float(step.get("error", 0.0)))

    # Expected no-leak effort based on setpoint / nominal target.
    # Using 0.923 as the baseline coefficient (observed in no-fault scenarios).
    if nominal_target > 0.0:
        ratio = active_setpoint / nominal_target
        ratio = max(0.1, min(1.5, ratio))
        expected_effort = 0.923 * (ratio ** 0.5)
    else:
        expected_effort = 0.923

    def median(vals):
        if not vals:
            return 0.0
        sv = sorted(vals)
        m = len(sv)
        if m % 2 == 1:
            return sv[m // 2]
        return (sv[m // 2 - 1] + sv[m // 2]) / 2.0

    # Window sizes
    short_n = min(5, n)
    long_n = min(20, n)

    recent_efforts_short = efforts[-short_n:]
    recent_efforts_long = efforts[-long_n:]
    recent_errors_short = errors[-short_n:]
    recent_errors_long = errors[-long_n:]

    med_effort_short = median(recent_efforts_short)
    med_effort_long = median(recent_efforts_long)
    med_error_short = median(recent_errors_short)
    med_error_long = median(recent_errors_long)

    dev_short = [e - expected_effort for e in recent_efforts_short]
    dev_long = [e - expected_effort for e in recent_efforts_long]

    med_dev_short = median(dev_short)
    med_dev_long = median(dev_long)
    mean_dev_short = sum(dev_short) / len(dev_short) if dev_short else 0.0
    mean_dev_long = sum(dev_long) / len(dev_long) if dev_long else 0.0
    max_dev_short = max(dev_short) if dev_short else 0.0

    # Robust noise estimate based on short-window effort MAD
    abs_dev_short = [abs(e - med_effort_short) for e in recent_efforts_short]
    mad_effort_short = median(abs_dev_short)
    robust_sigma = max(0.015, 1.4826 * mad_effort_short)

    # Thresholds (absolute deviations from expected)
    mild_threshold = 0.07
    moderate_threshold = 0.12
    severe_threshold = 0.35

    # Counts of deviations above thresholds
    short_exceed_mild = sum(1 for d in dev_short if d > mild_threshold)
    short_exceed_mod = sum(1 for d in dev_short if d > moderate_threshold)
    long_exceed_mild = sum(1 for d in dev_long if d > mild_threshold)
    long_exceed_mod = sum(1 for d in dev_long if d > moderate_threshold)

    # Detection logic
    fast_severe = (
        short_n >= 2 and
        max_dev_short > 0.6 and
        med_dev_short > severe_threshold and
        med_error_short < -0.03
    )

    persistent_severe = (
        short_n >= 3 and
        med_dev_short > severe_threshold and
        med_error_short < -0.08
    )

    standard_short = (
        short_n >= 4 and
        med_dev_short > moderate_threshold and
        short_exceed_mod >= max(2, int(short_n * 0.6)) and
        mean_dev_short > moderate_threshold * 0.75
    )

    standard_long = (
        long_n >= 8 and
        med_dev_long > mild_threshold and
        mean_dev_long > mild_threshold and
        long_exceed_mild >= max(4, int(long_n * 0.6))
    )

    mild_long = (
        long_n >= 10 and
        med_dev_long > 0.04 and
        mean_dev_long > 0.05 and
        long_exceed_mild >= max(5, int(long_n * 0.5)) and
        robust_sigma < 0.25
    )

    anomaly_flag = fast_severe or persistent_severe or standard_short or standard_long or mild_long

    # Clearing condition: recent effort is back near expected and error is not significant.
    clear_short = (
        short_n >= 3 and
        med_dev_short < 0.05 and
        short_exceed_mild <= 1 and
        abs(med_error_short) < 0.06
    )

    clear_long = (
        long_n >= 5 and
        med_dev_long < 0.04 and
        long_exceed_mild <= int(long_n * 0.25)
    )

    if clear_short or clear_long:
        anomaly_flag = False

    # Adjust setpoint based on severity and restore toward nominal when stable.
    if anomaly_flag:
        if med_dev_short > 0.6:
            adjustment = 0.2
        elif med_dev_short > 0.3:
            adjustment = 0.12
        else:
            adjustment = 0.08
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = (f"Anomaly detected: effort median {med_effort_short:.2f} "
                     f"(excess {med_dev_short:.2f}), lowering setpoint by {adjustment:.2f}.")
    else:
        if (active_setpoint < nominal_target and
            med_dev_short < 0.04 and
            abs(med_error_short) < 0.04 and
            mean_dev_long < 0.06):
            restore_step = min(0.08, nominal_target - active_setpoint)
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
