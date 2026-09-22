def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {'diagnosis': 'No telemetry data.', 'adjusted_setpoint': active_setpoint, 'anomaly_flag': False}

    efforts = [float(step.get('pump_effort', 0.0)) for step in telemetry_window]
    errors = [float(step.get('error', 0.0)) for step in telemetry_window]

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

    if nominal_target > 0.0:
        ratio = active_setpoint / nominal_target
        ratio = max(0.1, min(1.5, ratio))
        base_effort = 0.923 * (ratio ** 0.5)
    else:
        base_effort = 0.923

    error_comp = 0.8 * max(0.0, -med_error_short)
    expected_effort = base_effort + error_comp

    dev_short = [e - expected_effort for e in recent_efforts_short]
    dev_long = [e - expected_effort for e in recent_efforts_long]

    med_dev_short = median(dev_short)
    med_dev_long = median(dev_long)
    mean_dev_short = sum(dev_short) / len(dev_short) if dev_short else 0.0
    mean_dev_long = sum(dev_long) / len(dev_long) if dev_long else 0.0
    max_dev_short = max(dev_short) if dev_short else 0.0

    abs_dev_short = [abs(e - med_effort_short) for e in recent_efforts_short]
    mad_effort_short = median(abs_dev_short)
    robust_sigma = max(0.03, 1.4826 * mad_effort_short)

    mild_threshold = max(0.15, 3.5 * robust_sigma)
    moderate_threshold = max(0.25, 5.0 * robust_sigma)
    severe_threshold = max(0.45, 7.0 * robust_sigma)

    error_slope = 0.0
    if short_n >= 4:
        xs = list(range(short_n))
        mean_x = float(short_n - 1) / 2.0
        mean_y = sum(recent_errors_short) / float(short_n)
        cov = 0.0
        var_x = 0.0
        for i in range(short_n):
            dx = xs[i] - mean_x
            cov += dx * (recent_errors_short[i] - mean_y)
            var_x += dx * dx
        if var_x > 1e-9:
            error_slope = cov / var_x

    recovering = (
        short_n >= 5 and
        med_error_short < 0.0 and
        error_slope > 0.02 and
        med_effort_short < 1.5
    )

    short_exceed_mild = sum(1 for d in dev_short if d > mild_threshold)
    short_exceed_mod = sum(1 for d in dev_short if d > moderate_threshold)
    long_exceed_mild = sum(1 for d in dev_long if d > mild_threshold)

    cusum = 0.0
    for d in dev_short:
        cusum = max(0.0, cusum + (d - 0.05))
    cusum_trigger = cusum > 0.25

    min_history = 10

    persistent_mild = (
        n >= min_history and
        short_n >= 5 and
        med_dev_short > mild_threshold and
        short_exceed_mild >= max(4, int(short_n * 0.8)) and
        med_error_short < -0.03 and
        not recovering
    )

    moderate_short = (
        n >= min_history and
        short_n >= 5 and
        med_dev_short > moderate_threshold and
        short_exceed_mod >= max(3, int(short_n * 0.6)) and
        med_error_short < -0.02 and
        not recovering
    )

    severe_deviation = (
        short_n >= 3 and
        med_dev_short > severe_threshold and
        med_error_short < -0.02 and
        not recovering
    )

    severe_absolute = (
        short_n >= 3 and
        med_effort_short > 1.8 and
        med_error_short < -0.03 and
        not recovering
    )

    cusum_anomaly = (
        n >= min_history and
        cusum_trigger and
        med_error_short < -0.03 and
        not recovering
    )

    anomaly_flag = (
        persistent_mild or
        moderate_short or
        severe_deviation or
        severe_absolute or
        cusum_anomaly
    )

    last_dev = efforts[-1] - expected_effort if n >= 1 else 0.0
    last_error = errors[-1] if n >= 1 else 0.0

    clear_short = (
        short_n >= 5 and
        med_dev_short < mild_threshold * 0.5 and
        short_exceed_mild == 0 and
        abs(med_error_short) < 0.06
    )

    clear_long = (
        long_n >= 10 and
        med_dev_long < 0.10 and
        long_exceed_mild <= max(1, int(long_n * 0.2)) and
        abs(med_error_long) < 0.10
    )

    immediate_clear = (
        n >= 2 and
        abs(last_dev) < 0.10 and
        abs(last_error) < 0.10 and
        abs(med_effort_short - expected_effort) < 0.08
    )

    if clear_short or clear_long or immediate_clear:
        anomaly_flag = False

    if anomaly_flag:
        if med_dev_short > 0.5:
            adjustment = 0.15
        elif med_dev_short > 0.25:
            adjustment = 0.08
        else:
            adjustment = 0.04
        adjusted_setpoint = max(0.0, active_setpoint - adjustment)
        diagnosis = (
            f'Anomaly detected: effort median {med_effort_short:.2f} '
            f'(excess {med_dev_short:.2f}), lowering setpoint by {adjustment:.2f}.'
        )
    else:
        if (active_setpoint < nominal_target and
            med_dev_short < mild_threshold * 0.4 and
            abs(med_error_short) < 0.10 and
            mean_dev_long < 0.12):
            restore_step = min(0.04, nominal_target - active_setpoint)
            adjusted_setpoint = min(nominal_target, active_setpoint + restore_step)
            diagnosis = 'System stable and below nominal, restoring setpoint toward nominal.'
        else:
            adjusted_setpoint = active_setpoint
            diagnosis = 'Nominal operation, no action.'

    return {
        'diagnosis': diagnosis,
        'adjusted_setpoint': adjusted_setpoint,
        'anomaly_flag': anomaly_flag,
    }
