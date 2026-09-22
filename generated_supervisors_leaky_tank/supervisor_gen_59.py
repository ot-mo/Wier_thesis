def supervise(telemetry_window, active_setpoint, nominal_target):
    n = len(telemetry_window)
    if n == 0:
        return {'diagnosis': 'No telemetry data.', 'adjusted_setpoint': float(active_setpoint), 'anomaly_flag': False}

    efforts = []
    errors = []
    for step in telemetry_window:
        eff = 0.0
        err = 0.0
        if isinstance(step, dict):
            try:
                eff = float(step.get('pump_effort', 0.0))
            except (TypeError, ValueError):
                eff = 0.0
            try:
                err = float(step.get('error', 0.0))
            except (TypeError, ValueError):
                err = 0.0
        efforts.append(eff)
        errors.append(err)

    def med(vals):
        if not vals:
            return 0.0
        sv = sorted(vals)
        m = len(sv)
        if m % 2 == 1:
            return sv[m // 2]
        return 0.5 * (sv[m // 2 - 1] + sv[m // 2])

    def avg(vals):
        if not vals:
            return 0.0
        return sum(vals) / float(len(vals))

    def linear_slope(vals):
        m = len(vals)
        if m < 2:
            return 0.0
        mx = (m - 1) / 2.0
        my = avg(vals)
        cov = 0.0
        var = 0.0
        for i in range(m):
            dx = i - mx
            cov += dx * (vals[i] - my)
            var += dx * dx
        if var <= 1e-9:
            return 0.0
        return cov / var

    short_n = min(5, n)
    mid_n = min(10, n)
    long_n = min(20, n)

    e_short = efforts[-short_n:]
    e_mid = efforts[-mid_n:]
    e_long = efforts[-long_n:]
    r_short = errors[-short_n:]

    med_effort_short = med(e_short)
    med_effort_mid = med(e_mid)
    med_effort_long = med(e_long)

    if nominal_target and nominal_target > 0.0:
        ratio = active_setpoint / nominal_target
        ratio = max(0.1, min(1.5, ratio))
        base_effort = 0.923 * (ratio ** 0.5)
    else:
        base_effort = 0.923

    expected_effort = base_effort

    dev_short = [x - expected_effort for x in e_short]
    dev_mid = [x - expected_effort for x in e_mid]
    dev_long = [x - expected_effort for x in e_long]

    med_dev_short = med(dev_short)
    med_dev_mid = med(dev_mid)
    med_dev_long = med(dev_long)
    max_dev_short = max(dev_short) if dev_short else 0.0

    mad = med([abs(x - med_effort_long) for x in e_long])
    sigma = max(0.02, 1.4826 * mad)

    t_low = max(0.05, 2.0 * sigma)
    t_mild = max(0.10, 3.5 * sigma)
    t_mod = max(0.20, 5.0 * sigma)
    t_sev = max(0.45, 8.0 * sigma)

    frac_short = sum(1 for d in dev_short if d > t_low) / float(short_n)
    frac_mid = sum(1 for d in dev_mid if d > t_low) / float(mid_n)
    frac_long = sum(1 for d in dev_long if d > t_low) / float(long_n)

    med_error_short = med(r_short) if r_short else 0.0
    err_slope = linear_slope(r_short) if len(r_short) >= 2 else 0.0

    refilling = (nominal_target and active_setpoint < nominal_target - 1e-9 and
                 med_error_short < -0.02 and err_slope > 0.01)

    if refilling:
        t_mild *= 1.5
        t_mod *= 1.5
        t_sev *= 1.5

    acute = (short_n >= 4 and
             med_dev_short > 0.8 * t_mod and
             max_dev_short > t_sev and
             frac_short >= 0.5)

    persistent = (long_n >= 12 and
                  med_dev_long > t_mod and
                  med_dev_short > 0.5 * t_mod and
                  frac_long >= 0.7)

    sustained = (mid_n >= 8 and
                 med_dev_mid > t_mild and
                 med_dev_long > 0.5 * t_mild and
                 frac_mid >= 0.7)

    anomaly_flag = bool(acute or persistent or sustained)

    if short_n >= 3 and med_dev_short < 0.3 * t_mild and frac_short <= 0.2:
        anomaly_flag = False

    if refilling and med_dev_short < 1.0 and max_dev_short < 1.4:
        anomaly_flag = False

    if anomaly_flag:
        if med_dev_short > 0.6:
            drop = 0.20
        elif med_dev_short > 0.3:
            drop = 0.12
        else:
            drop = 0.06
        floor = 0.0
        if nominal_target and nominal_target > 0.0:
            floor = 0.4 * nominal_target
        adjusted_setpoint = active_setpoint - drop
        if adjusted_setpoint < floor:
            adjusted_setpoint = floor
        if adjusted_setpoint < 0.0:
            adjusted_setpoint = 0.0
        adjusted_setpoint = float(adjusted_setpoint)
        diagnosis = ('Anomaly detected: pump effort median %.3f vs expected %.3f (excess %.3f); lowering setpoint by %.2f.'
                     % (med_effort_short, expected_effort, med_dev_short, drop))
    else:
        if nominal_target and active_setpoint < nominal_target - 1e-9:
            gap = nominal_target - active_setpoint
            step_up = 0.3 * gap
            if step_up < 0.05:
                step_up = 0.05
            if step_up > 0.15:
                step_up = 0.15
            if step_up > gap:
                step_up = gap
            adjusted_setpoint = active_setpoint + step_up
            if adjusted_setpoint > nominal_target:
                adjusted_setpoint = nominal_target
            adjusted_setpoint = float(adjusted_setpoint)
            diagnosis = ('Nominal operation; restoring setpoint toward nominal (%.3f -> %.3f).'
                         % (active_setpoint, adjusted_setpoint))
        else:
            adjusted_setpoint = float(active_setpoint)
            diagnosis = 'Nominal operation, no action.'

    return {
        'diagnosis': diagnosis,
        'adjusted_setpoint': adjusted_setpoint,
        'anomaly_flag': anomaly_flag,
    }