def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {'diagnosis': 'No telemetry data.', 'adjusted_setpoint': active_setpoint, 'anomaly_flag': False}

    def median(vals):
        if not vals:
            return 0.0
        sv = sorted(vals)
        m = len(sv)
        if m % 2 == 1:
            return sv[m // 2]
        return (sv[m // 2 - 1] + sv[m // 2]) / 2.0

    def compute_slope(vals):
        m = len(vals)
        if m < 2:
            return 0.0
        xs = list(range(m))
        mean_x = (m - 1) / 2.0
        mean_y = sum(vals) / m
        cov = 0.0
        var_x = 0.0
        for i in range(m):
            dx = xs[i] - mean_x
            cov += dx * (vals[i] - mean_y)
            var_x += dx * dx
        if var_x > 1e-9:
            return cov / var_x
        return 0.0

    efforts = []
    errors = []
    for step in telemetry_window:
        e = step.get('pump_effort', 0.0)
        if e is None:
            e = 0.0
        efforts.append(float(e))
        er = step.get('error', 0.0)
        if er is None:
            er = 0.0
        errors.append(float(er))

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

    if nominal_target > 0.0:
        ratio = active_setpoint / nominal_target
        ratio = max(0.1, min(1.5, ratio))
        base_effort = 0.923 * (ratio ** 0.5)
    else:
        base_effort = 0.923

    dev_short = [e - base_effort for e in recent_efforts_short]
    dev_long = [e - base_effort for e in recent_efforts_long]

    med_dev_short = median(dev_short)
    med_dev_long = median(dev_long)
    mean_dev_short = sum(dev_short) / len(dev_short) if dev_short else 0.0
    mean_dev_long = sum(dev_long) / len(dev_long) if dev_long else 0.0
    max_dev_short = max(dev_short) if dev_short else 0.0

    abs_dev_short = [abs(e - med_effort_short) for e in recent_efforts_short]
    mad_effort_short = median(abs_dev_short)
    robust_sigma = max(0.015, 1.4826 * mad_effort_short)

    mild_threshold = max(0.08, 2.0 * robust_sigma)
    moderate_threshold = max(0.16, 3.0 * robust_sigma)
    severe_threshold = max(0.35, 4.5 * robust_sigma)

    error_slope = compute_slope(recent_errors_short)
    effort_slope = compute_slope(recent_efforts_short)

    filling_suppression = (short_n >= 5 and med_error_short < -0.02 and error_slope > 0.01 and med_effort_short < 1.8)

    recovering = (short_n >= 3 and effort_slope < -0.01 and (error_slope > 0.0 or abs(med_error_short) < 0.05))

    short_exceed_mild = sum(1 for d in dev_short if d > mild_threshold)
    short_exceed_mod = sum(1 for d in dev_short if d > moderate_threshold)
    long_exceed_mild = sum(1 for d in dev_long if d > mild_threshold)
    long_exceed_mod = sum(1 for d in dev_long if d > moderate_threshold)

    rapid_onset = (n >= 3 and max_dev_short > 0.45 and med_error_short < -0.02 and error_slope < -0.01 and not filling_suppression and not recovering)
    severe_deviation = (short_n >= 3 and med_dev_short > severe_threshold and med_error_short < -0.02 and not filling_suppression and not recovering)
    persistent_severe = (short_n >= 5 and med_dev_short > severe_threshold and med_error_short < -0.04 and not filling_suppression and not recovering)
    extreme_dev = (short_n >= 3 and max_dev_short > 1.0 and med_dev_short > 0.4 and not filling_suppression and not recovering)
    standard_short = (short_n >= 5 and med_dev_short > moderate_threshold and short_exceed_mod >= max(2, int(short_n * 0.6)) and mean_dev_short > moderate_threshold * 0.75 and not filling_suppression and not recovering)
    standard_long = (long_n >= 10 and med_dev_long > mild_threshold and mean_dev_long > mild_threshold and long_exceed_mild >= max(5, int(long_n * 0.7)) and not filling_suppression and not recovering)
    mild_long = (long_n >= 15 and med_dev_long > 0.04 and mean_dev_long > 0.05 and long_exceed_mild >= max(4, int(long_n * 0.5)) and robust_sigma < 0.25 and not filling_suppression and not recovering)

    anomaly_flag = (rapid_onset or severe_deviation or persistent_severe or extreme_dev or standard_short or standard_long or mild_long)

    last_dev = efforts[-1] - base_effort if n >= 1 else 0.0
    last_error = errors[-1] if n >= 1 else 0.0

    clear_short = (short_n >= 3 and med_dev_short < mild_threshold and short_exceed_mild <= 0 and abs(med_error_short) < 0.08)
    clear_long = (long_n >= 5 and med_dev_long < mild_threshold and long_exceed_mild <= max(1, int(long_n * 0.2)) and abs(med_error_long) < 0.08)
    immediate_clear = (n >= 2 and abs(last_dev) < 0.10 and abs(last_error) < 0.10 and abs(med_effort_short - base_effort) < 0.10)
    trend_clear = (short_n >= 3 and recovering and med_dev_short < moderate_threshold)

    if clear_short or clear_long or immediate_clear or trend_clear:
        anomaly_flag = False

    if anomaly_flag:
        reduction = nominal_target - active_setpoint if nominal_target > 0 else 0.0
        max_reduction = 0.6 * nominal_target if nominal_target > 0 else 1.0
        if reduction >= max_reduction:
            adjustment = 0.0
        else:
            if med_dev_short > 0.6:
                adjustment = 0.15
            elif med_dev_short > 0.3:
                adjustment = 0.10
            else:
                adjustment = 0.05
            if recovering:
                adjustment = 0.0
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = f'Anomaly detected: effort median {med_effort_short:.2f} (excess {med_dev_short:.2f}), lowering setpoint by {adjustment:.2f}.'
    else:
        if active_setpoint < nominal_target:
            if (short_n >= 3 and abs(med_dev_short) < 0.05 and abs(med_error_short) < 0.05 and mean_dev_long < 0.1):
                restore_step = min(0.15, nominal_target - active_setpoint)
            else:
                restore_step = min(0.08, nominal_target - active_setpoint)
            adjusted_setpoint = min(nominal_target, active_setpoint + restore_step)
            diagnosis = 'System stable and below nominal, restoring setpoint toward nominal.'
        elif active_setpoint > nominal_target:
            adjusted_setpoint = nominal_target
            diagnosis = 'Setpoint above nominal, clamping to nominal.'
        else:
            adjusted_setpoint = active_setpoint
            diagnosis = 'Nominal operation, no action.'

    return {
        'diagnosis': diagnosis,
        'adjusted_setpoint': adjusted_setpoint,
        'anomaly_flag': anomaly_flag,
    }