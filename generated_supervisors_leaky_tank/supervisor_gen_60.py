def supervise(telemetry_window, active_setpoint, nominal_target):
    try:
        active_setpoint = float(active_setpoint)
    except (TypeError, ValueError):
        active_setpoint = 0.0
    try:
        nominal_target = float(nominal_target)
    except (TypeError, ValueError):
        nominal_target = 0.0

    n = len(telemetry_window)
    if n == 0:
        return {'diagnosis': 'No telemetry data.',
                'adjusted_setpoint': float(active_setpoint),
                'anomaly_flag': False}

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

    short_n = min(6, n)
    mid_n = min(12, n)
    long_n = min(24, n)

    e_s = efforts[-short_n:]
    e_m = efforts[-mid_n:]
    e_l = efforts[-long_n:]
    r_s = errors[-short_n:]
    r_m = errors[-mid_n:]

    med_err_s = med(r_s)
    err_slope_s = linear_slope(r_s)
    err_slope_m = linear_slope(r_m)

    if nominal_target > 0.0:
        ratio = active_setpoint / nominal_target
        if ratio < 0.2:
            ratio = 0.2
        elif ratio > 1.5:
            ratio = 1.5
        base_effort = 0.923 * (ratio ** 0.5)
    else:
        base_effort = 0.923

    headroom = 0.2 * max(0.0, -med_err_s)
    if headroom > 0.12:
        headroom = 0.12
    expected_effort = base_effort + headroom

    dev_s = [v - expected_effort for v in e_s]
    dev_m = [v - expected_effort for v in e_m]
    dev_l = [v - expected_effort for v in e_l]

    med_s = med(dev_s)
    med_m = med(dev_m)
    med_l = med(dev_l)
    max_s = max(dev_s) if dev_s else 0.0

    med_eff_l = med(e_l)
    mad_l = med([abs(v - med_eff_l) for v in e_l])
    sigma = 1.4826 * mad_l
    if sigma < 0.03:
        sigma = 0.03
    elif sigma > 0.35:
        sigma = 0.35

    thr = 3.0 * sigma
    if thr < 0.24:
        thr = 0.24

    hi = 3.0 * sigma
    if hi < 0.5:
        hi = 0.5
    spike = 4.0 * sigma
    if spike < 0.85:
        spike = 0.85

    refilling = (med_err_s < -0.03 and (err_slope_s > 0.0 or err_slope_m > 0.01))
    thr_use = thr * 1.3 if refilling else thr

    def frac_gt(vals, level):
        if not vals:
            return 0.0
        c = 0
        for v in vals:
            if v > level:
                c += 1
        return c / float(len(vals))

    frac_s = frac_gt(dev_s, 0.5 * thr)
    frac_m = frac_gt(dev_m, 0.5 * thr)
    frac_l = frac_gt(dev_l, 0.5 * thr)

    anomaly_flag = False

    if short_n >= 3 and med_s > hi and frac_s >= 0.5:
        anomaly_flag = True
    if short_n >= 4 and med_s > 0.28 and med_s > 1.5 * sigma and max_s > spike:
        anomaly_flag = True
    if (short_n >= 5 and mid_n >= 8 and
            med_s > thr_use and med_m > 0.75 * thr_use and
            frac_s >= 0.6 and frac_m >= 0.6):
        anomaly_flag = True
    if (mid_n >= 10 and long_n >= 14 and
            med_m > 0.85 * thr_use and med_l > 0.65 * thr_use and
            frac_m >= 0.6 and frac_l >= 0.55):
        anomaly_flag = True

    if short_n >= 3 and med_s < 0.30 * thr and frac_s <= 0.34:
        anomaly_flag = False

    if refilling and med_s < hi and max_s < spike:
        strong_persistent = (long_n >= 16 and med_l > 0.6 and frac_l >= 0.7)
        if not strong_persistent:
            anomaly_flag = False

    if anomaly_flag:
        frac_drop = 0.22 * med_s
        if frac_drop < 0.05:
            frac_drop = 0.05
        elif frac_drop > 0.30:
            frac_drop = 0.30
        if nominal_target > 0.0:
            target = nominal_target * (1.0 - frac_drop)
            floor = 0.5 * nominal_target
            if target < floor:
                target = floor
        else:
            target = active_setpoint - frac_drop
            if target < 0.0:
                target = 0.0
        adjusted_setpoint = active_setpoint
        if target < adjusted_setpoint:
            adjusted_setpoint = target
        if adjusted_setpoint < 0.0:
            adjusted_setpoint = 0.0
        adjusted_setpoint = float(adjusted_setpoint)
        diagnosis = ('Anomaly: median pump effort %.3f vs expected %.3f '
                     '(excess %.3f); setpoint at %.3f.'
                     % (med(e_s), expected_effort, med_s, adjusted_setpoint))
    else:
        if nominal_target > 0.0 and active_setpoint < nominal_target - 1e-9:
            gap = nominal_target - active_setpoint
            step_up = 0.4 * gap
            if step_up < 0.10:
                step_up = 0.10
            if step_up > 0.30:
                step_up = 0.30
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

    return {'diagnosis': diagnosis,
            'adjusted_setpoint': adjusted_setpoint,
            'anomaly_flag': anomaly_flag}
