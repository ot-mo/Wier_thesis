def supervise(telemetry_window, active_setpoint, nominal_target):
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
            if ('error' not in step) and ('level' in step) and ('setpoint' in step):
                try:
                    err = float(step.get('level')) - float(step.get('setpoint'))
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

    def slope(vals):
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

    def frac_gt(vals, thr):
        if not vals:
            return 0.0
        return sum(1 for v in vals if v > thr) / float(len(vals))

    short_n = min(5, n)
    long_n = min(20, n)

    err_l = errors[-long_n:]
    med_err_long = med(err_l)
    slope_err_long = slope(err_l)

    # ---- hydraulic expectation at the current operating point ----------
    base_effort = 0.923
    if nominal_target and nominal_target > 0.0:
        ratio = active_setpoint / nominal_target
        if ratio < 0.1:
            ratio = 0.1
        if ratio > 1.5:
            ratio = 1.5
        base_effort = 0.923 * (ratio ** 0.5)

    # Refilling / recovery allowance.  When the level still sits below its
    # setpoint AND is climbing, the loop legitimately commands more effort
    # than the steady-state hydraulic model.  This is the ONLY reason a
    # sustained effort excess is not a leak, so it is proportional to the
    # level deficit and the climb rate, and hard capped: it can delay a
    # mild leak by a moment but can never swallow a real one.
    deficit = max(0.0, -med_err_long)
    climb = max(0.0, slope_err_long)
    allowance = 1.1 * deficit + 4.0 * climb
    if allowance > 0.45:
        allowance = 0.45
    expected_effort = base_effort + allowance

    resid = [efforts[i] - expected_effort for i in range(n)]
    res_s = resid[-short_n:]
    res_l = resid[-long_n:]

    med_s = med(res_s)
    med_l = med(res_l)
    max_s = max(res_s) if res_s else 0.0

    mad = med([abs(v - med_s) for v in res_s])
    sigma = 1.4826 * mad
    if sigma < 0.02:
        sigma = 0.02

    t1 = 3.0 * sigma
    if t1 < 0.10:
        t1 = 0.10
    t2 = 4.5 * sigma
    if t2 < 0.20:
        t2 = 0.20
    t3 = 7.0 * sigma
    if t3 < 0.45:
        t3 = 0.45

    fr_s = frac_gt(res_s, t1)
    fr_l = frac_gt(res_l, t1)

    if long_n >= 3:
        head_l = med(res_l[:3])
        tail_l = med(res_l[-3:])
    else:
        head_l = med_l
        tail_l = med_l

    # ---- detectors -----------------------------------------------------
    # onset: the residual steps up against an otherwise calm window
    onset = False
    if long_n >= 8:
        if (tail_l - head_l) > max(0.22, t2) and tail_l > t1 and med_s > t1:
            onset = True

    sustained = (short_n >= 5 and long_n >= 8 and
                 med_s > t1 and med_l > 0.6 * t1 and
                 fr_s >= 0.6 and fr_l >= 0.6)

    persistent = (long_n >= 12 and
                  med_l > t2 and med_s > 0.6 * t2 and fr_l >= 0.7)

    acute = (short_n >= 3 and med_s > 0.8 * t2 and max_s > t3)

    anomaly_flag = bool(onset or sustained or persistent or acute)

    # ---- suppressions --------------------------------------------------
    # a fully clean recent window overrides the slow long-horizon detectors
    if anomaly_flag and (not onset) and (not acute):
        if (short_n >= 4 and med_s < 0.3 * t1 and fr_s <= 0.2 and
                med_l < 0.8 * t1 and fr_l <= 0.4):
            anomaly_flag = False

    # a residual that is clearly fading across the window is the tail of a
    # filling / recovery transient, not a new fault
    if anomaly_flag and (not onset) and (not acute) and long_n >= 8:
        drop_win = head_l - tail_l
        if drop_win > 0.08 and tail_l < 0.55 * head_l and med_s < t2:
            anomaly_flag = False

    # ---- supervisory action --------------------------------------------
    if anomaly_flag:
        if med_s > 1.0:
            drop = 0.15
        elif med_s > 0.35:
            drop = 0.10
        else:
            drop = 0.05
        floor = 0.0
        if nominal_target and nominal_target > 0.0:
            floor = 0.4 * nominal_target
        adjusted_setpoint = active_setpoint - drop
        if adjusted_setpoint < floor:
            adjusted_setpoint = floor
        if adjusted_setpoint < 0.0:
            adjusted_setpoint = 0.0
        adjusted_setpoint = float(adjusted_setpoint)
        diagnosis = ('Leak anomaly: effort median %.3f vs expected %.3f '
                     '(excess %.3f, allowance %.3f); lowering setpoint %.3f -> %.3f.'
                     % (med(efforts[-short_n:]), expected_effort, med_s,
                        allowance, active_setpoint, adjusted_setpoint))
    else:
        if nominal_target and nominal_target > 0.0 and active_setpoint < nominal_target - 1e-9:
            gap = nominal_target - active_setpoint
            step_up = 0.5 * gap
            if step_up < 0.15:
                step_up = 0.15
            if step_up > 0.35:
                step_up = 0.35
            if step_up > gap:
                step_up = gap
            adjusted_setpoint = active_setpoint + step_up
            if adjusted_setpoint > nominal_target:
                adjusted_setpoint = nominal_target
            adjusted_setpoint = float(adjusted_setpoint)
            diagnosis = ('Nominal operation; restoring setpoint toward nominal '
                         '(%.3f -> %.3f).' % (active_setpoint, adjusted_setpoint))
        else:
            adjusted_setpoint = float(active_setpoint)
            diagnosis = 'Nominal operation, no action.'

    return {'diagnosis': diagnosis,
            'adjusted_setpoint': adjusted_setpoint,
            'anomaly_flag': anomaly_flag}
