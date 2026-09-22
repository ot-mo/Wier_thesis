def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    efforts = []
    errors = []
    for step in telemetry_window:
        efforts.append(float(step.get("pump_effort", 0.0)))
        errors.append(float(step.get("error", 0.0)))

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

    abs_dev_short = [abs(e - med_effort_short) for e in recent_efforts_short]
    mad_effort_short = median(abs_dev_short)
    robust_sigma = max(0.015, 1.4826 * mad_effort_short)

    sigma_for_threshold = min(robust_sigma, 0.08)
    mild_threshold = max(0.045, 2.5 * sigma_for_threshold)
    moderate_threshold = max(0.08, 3.5 * sigma_for_threshold)
    severe_threshold = max(0.22, 5.0 * sigma_for_threshold)

    error_trend = 0.0
    if short_n >= 4:
        half = short_n // 2
        first_half_med = median(recent_errors_short[:half])
        second_half_med = median(recent_errors_short[half:])
        error_trend = second_half_med - first_half_med

    filling_suppression = (
        short_n >= 4 and med_error_short < -0.04 and error_trend > 0.01
    )

    level_above_setpoint = med_error_short > 0.03
    near_nominal = (active_setpoint >= 0.9 * nominal_target) if nominal_target > 0.0 else True

    short_exceed_mild = sum(1 for d in dev_short if d > mild_threshold)
    short_exceed_mod = sum(1 for d in dev_short if d > moderate_threshold)
    long_exceed_mild = sum(1 for d in dev_long if d > mild_threshold)
    long_exceed_mod = sum(1 for d in dev_long if d > moderate_threshold)
    long_exceed_002 = sum(1 for d in dev_long if d > 0.02)

    extreme_dev = (
        short_n >= 2 and
        max_dev_short > 1.0 and
        med_dev_short > 0.5 and
        not filling_suppression and
        not level_above_setpoint
    )

    fast_severe = (
        short_n >= 2 and
        max_dev_short > 0.4 and
        med_dev_short > severe_threshold and
        med_error_short <= 0.02 and
        not filling_suppression and
        not level_above_setpoint
    )

    persistent_severe = (
        short_n >= 3 and
        med_dev_short > severe_threshold and
        med_error_short <= 0.02 and
        not filling_suppression and
        not level_above_setpoint
    )

    compensated_short = (
        short_n >= 5 and
        med_dev_short > moderate_threshold and
        short_exceed_mod >= max(3, int(short_n * 0.7)) and
        abs(med_error_short) < 0.08 and
        near_nominal and
        not filling_suppression and
        not level_above_setpoint
    )

    standard_long = (
        long_n >= 10 and
        med_dev_long > mild_threshold and
        mean_dev_long > mild_threshold and
        long_exceed_mild >= max(5, int(long_n * 0.7)) and
        abs(med_error_long) < 0.10 and
        near_nominal and
        not filling_suppression and
        med_error_short <= 0.02
    )

    persistent_mild = (
        long_n >= 15 and
        med_dev_long > 0.025 and
        mean_dev_long > 0.03 and
        long_exceed_002 >= max(8, int(long_n * 0.8)) and
        robust_sigma < 0.12 and
        abs(med_error_long) < 0.08 and
        near_nominal and
        not filling_suppression and
        med_error_short <= 0.02
    )

    anomaly_flag = (
        extreme_dev or fast_severe or persistent_severe or
        compensated_short or standard_long or persistent_mild
    )

    clear_short = (
        short_n >= 5 and
        med_dev_short < 0.03 and
        short_exceed_mild <= max(1, int(short_n * 0.2)) and
        abs(med_error_short) < 0.06
    )

    clear_long = (
        long_n >= 8 and
        med_dev_long < 0.03 and
        long_exceed_mild <= max(2, int(long_n * 0.25)) and
        abs(med_error_long) < 0.08
    )

    clear_level_above = med_error_short > 0.04 and med_error_long > 0.02

    if clear_short or clear_long or clear_level_above:
        anomaly_flag = False

    if anomaly_flag:
        if med_dev_short > 0.5:
            adjustment = 0.25
        elif med_dev_short > 0.25:
            adjustment = 0.15
        else:
            adjustment = 0.08
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = (f"Anomaly detected: effort median {med_effort_short:.2f} "
                     f"(excess {med_dev_short:.2f}), lowering setpoint by {adjustment:.2f}.")
    else:
        if (active_setpoint < nominal_target and
            med_dev_short < 0.02 and
            abs(med_error_short) < 0.04 and
            mean_dev_long < 0.04):
            restore_step = min(0.06, nominal_target - active_setpoint)
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