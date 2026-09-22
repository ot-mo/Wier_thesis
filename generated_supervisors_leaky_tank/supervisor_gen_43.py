def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {"diagnosis": "No telemetry data.", "adjusted_setpoint": active_setpoint, "anomaly_flag": False}

    try:
        active_setpoint = float(active_setpoint)
        nominal_target = float(nominal_target)
    except (TypeError, ValueError):
        active_setpoint = 1.0
        nominal_target = 1.0

    efforts = []
    errors = []
    for step in telemetry_window:
        eff = 0.0
        err = 0.0
        if isinstance(step, dict):
            try:
                eff = float(step.get("pump_effort", 0.0))
            except (TypeError, ValueError):
                eff = 0.0
            try:
                err = float(step.get("error", 0.0))
            except (TypeError, ValueError):
                err = 0.0
        efforts.append(eff)
        errors.append(err)

    if nominal_target > 1e-9:
        ratio = active_setpoint / nominal_target
        ratio = max(0.1, min(1.5, ratio))
        expected_effort = 0.923 * (ratio ** 0.5)
    else:
        expected_effort = 0.923

    deviations = [e - expected_effort for e in efforts]

    def median(vals):
        if not vals:
            return 0.0
        sv = sorted(vals)
        m = len(sv)
        if m % 2 == 1:
            return sv[m // 2]
        return (sv[m // 2 - 1] + sv[m // 2]) / 2.0

    def mad(vals):
        if not vals:
            return 0.0
        med = median(vals)
        return median([abs(v - med) for v in vals])

    def cusum(vals, k=0.03):
        s = 0.0
        for v in vals:
            s = max(0.0, s + v - k)
        return s

    short_k = min(5, n)
    mid_k = min(8, n)
    long_k = min(12, n)

    short_devs = deviations[-short_k:]
    mid_devs = deviations[-mid_k:]
    long_devs = deviations[-long_k:]
    short_errors = errors[-short_k:]
    mid_errors = errors[-mid_k:]

    med_short = median(short_devs)
    med_mid = median(mid_devs)
    med_long = median(long_devs)
    mad_short = mad(short_devs)
    mad_mid = mad(mid_devs)
    mad_long = mad(long_devs)
    med_err_short = median(short_errors)
    med_err_mid = median(mid_errors)

    def dynamic_threshold(mad_val, floor=0.04):
        return max(floor, 2.5 * mad_val + 0.02)

    thresh_short = dynamic_threshold(mad_short)
    thresh_mid = dynamic_threshold(mad_mid, floor=0.04)
    thresh_long = dynamic_threshold(mad_long, floor=0.05)

    small_positive = 0.04
    consistency_short = sum(1 for d in short_devs if d > small_positive)
    consistency_mid = sum(1 for d in mid_devs if d > small_positive)
    consistency_long = sum(1 for d in long_devs if d > small_positive)

    max_short = max(short_devs) if short_devs else 0.0
    max_mid = max(mid_devs) if mid_devs else 0.0

    severe = (max_short > 0.5 and med_err_short < -0.03) or (med_short > 0.6 and med_err_short < -0.05)

    cusum_short = cusum(short_devs, k=0.03)
    cusum_mid = cusum(mid_devs, k=0.03)

    detect_short = med_short > thresh_short and consistency_short == short_k
    detect_mid = med_mid > thresh_mid and consistency_mid >= max(3, mid_k - 1)
    detect_long = (len(long_devs) >= 5 and med_long > thresh_long and consistency_long >= max(5, long_k - 3))
    detect_cusum_short = cusum_short > 0.30 and max_short > 0.12
    detect_cusum_mid = cusum_mid > 0.40 and max_mid > 0.15

    if n < 5:
        detect_short = med_short > 0.12
        detect_mid = med_mid > 0.15
        detect_long = False
        detect_cusum_short = False
        detect_cusum_mid = False

    anomaly_flag = (detect_short or detect_mid or detect_long or
                    detect_cusum_short or detect_cusum_mid or severe)

    clear_short = (med_short < 0.03 and consistency_short <= 1 and med_err_short > -0.02)
    clear_mid = (med_mid < 0.04 and consistency_mid <= 2 and med_err_mid > -0.02)

    if clear_short or clear_mid:
        anomaly_flag = False

    if anomaly_flag:
        severity = med_short
        if med_mid > severity:
            severity = med_mid
        if severe or severity > 0.6:
            adjustment = 0.2
        elif severity > 0.3:
            adjustment = 0.15
        elif severity > 0.12:
            adjustment = 0.08
        else:
            adjustment = 0.03
        if detect_long and not detect_short and adjustment < 0.08:
            adjustment = 0.05
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = (f"Anomaly detected: effort median {med_short:.2f} "
                     f"(excess {med_short:.2f}), lowering setpoint by {adjustment:.2f}.")
    else:
        if (active_setpoint < nominal_target and
            med_short < 0.04 and
            abs(med_err_short) < 0.04):
            restore_step = min(0.05, nominal_target - active_setpoint)
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