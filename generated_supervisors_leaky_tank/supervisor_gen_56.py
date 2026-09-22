def supervise(telemetry_window, active_setpoint, nominal_target):
    try:
        sp = float(active_setpoint)
    except (TypeError, ValueError):
        sp = 0.0
    if sp != sp or sp > 1e9 or sp < -1e9:
        sp = 0.0
    try:
        nom = float(nominal_target)
    except (TypeError, ValueError):
        nom = 0.0
    if nom != nom or nom <= 0.0 or nom > 1e9:
        nom = 0.0

    try:
        steps = list(telemetry_window)
    except TypeError:
        steps = []
    n = len(steps)
    if n == 0:
        return {'diagnosis': 'No telemetry data; holding setpoint %.3f.' % sp,
                'adjusted_setpoint': float(sp),
                'anomaly_flag': False}

    efforts = []
    errors = []
    for step in steps:
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
        if eff != eff or eff > 1e9 or eff < -1e9:
            eff = 0.0
        if err != err or err > 1e9 or err < -1e9:
            err = 0.0
        efforts.append(eff)
        errors.append(err)

    def med(vals):
        m = len(vals)
        if m == 0:
            return 0.0
        sv = sorted(vals)
        h = m // 2
        if m % 2 == 1:
            return sv[h]
        return 0.5 * (sv[h - 1] + sv[h])

    k_s = min(n, 5)
    k_m = min(n, 10)
    k_l = min(n, 20)
    e_s = efforts[-k_s:]
    e_m = efforts[-k_m:]
    e_l = efforts[-k_l:]
    r_s = errors[-k_s:]

    if nom > 0.0:
        ref = sp
        lo = 0.8 * nom
        if ref < lo:
            ref = lo
        ratio = ref / nom
        if ratio < 0.25:
            ratio = 0.25
        elif ratio > 1.5:
            ratio = 1.5
        base = 0.923 * (ratio ** 0.5)
    else:
        base = 0.923

    err_mag = med(r_s)
    if err_mag < 0.0:
        err_mag = -err_mag
    if err_mag > 0.4:
        err_mag = 0.4
    expected = base + 0.75 * err_mag

    dev_s = [x - expected for x in e_s]
    dev_m = [x - expected for x in e_m]
    dev_l = [x - expected for x in e_l]
    med_dev_s = med(dev_s)
    med_dev_m = med(dev_m)
    med_dev_l = med(dev_l)
    max_dev_s = max(dev_s)

    med_eff_s = med(e_s)
    mad = med([abs(x - med_eff_s) for x in e_s])
    sigma = 1.4826 * mad
    if sigma < 0.02:
        sigma = 0.02
    elif sigma > 0.05:
        sigma = 0.05

    t_mild = 3.0 * sigma
    if t_mild < 0.14:
        t_mild = 0.14
    t_sev = 6.0 * sigma
    if t_sev < 0.32:
        t_sev = 0.32
    bar = 0.6 * t_mild

    c_s = 0
    for d in dev_s:
        if d > bar:
            c_s += 1
    c_m = 0
    for d in dev_m:
        if d > bar:
            c_m += 1
    c_l = 0
    for d in dev_l:
        if d > bar:
            c_l += 1
    frac_s = c_s / float(k_s)
    frac_m = c_m / float(k_m)
    frac_l = c_l / float(k_l)

    critical = (n >= 3 and med_dev_s > 0.8)
    acute = (n >= 3 and med_dev_s > t_sev and max_dev_s > 1.35 * t_sev and frac_s >= 0.6)
    sustained = (n >= 5 and med_dev_s > t_mild and frac_s >= 0.6 and med_dev_m > 0.5 * t_mild)
    chronic = (n >= 12 and med_dev_m > 0.7 * t_mild and frac_m >= 0.6 and med_dev_l > 0.45 * t_mild)
    anomaly_flag = bool(critical or acute or sustained or chronic)

    if anomaly_flag and not critical and not acute:
        if med_dev_s < 0.5 * t_mild and frac_s <= 0.4:
            anomaly_flag = False

    if nom > 0.0:
        floor_sp = 0.8 * nom
    else:
        floor_sp = 0.0

    if anomaly_flag:
        if med_dev_s > 1.2:
            drop = 0.08
        elif med_dev_s > 0.5:
            drop = 0.06
        elif med_dev_s > 0.25:
            drop = 0.04
        else:
            drop = 0.03
        new_sp = sp - drop
        if new_sp < floor_sp:
            new_sp = floor_sp
        if new_sp < 0.0:
            new_sp = 0.0
        diagnosis = 'Leak suspected: pump effort median %.3f vs expected %.3f (excess %.3f, sigma %.3f); setpoint %.3f -> %.3f.' % (med_eff_s, expected, med_dev_s, sigma, sp, new_sp)
    else:
        new_sp = sp
        if (nom > 0.0 and sp < nom - 1e-9 and
                med_dev_s < 0.5 * t_mild and frac_s <= 0.4 and med_dev_m < 0.5 * t_mild):
            gap = nom - sp
            step_up = 0.25 * gap
            if step_up < 0.05:
                step_up = 0.05
            elif step_up > 0.12:
                step_up = 0.12
            if step_up > gap:
                step_up = gap
            new_sp = sp + step_up
            if new_sp > nom:
                new_sp = nom
            diagnosis = 'Nominal: pump effort median %.3f matches expected %.3f; restoring setpoint %.3f -> %.3f.' % (med_eff_s, expected, sp, new_sp)
        else:
            diagnosis = 'Nominal: pump effort median %.3f matches expected %.3f; holding setpoint %.3f.' % (med_eff_s, expected, sp)

    if new_sp != new_sp:
        new_sp = sp
    if new_sp < 0.0:
        new_sp = 0.0

    return {'diagnosis': diagnosis,
            'adjusted_setpoint': float(new_sp),
            'anomaly_flag': anomaly_flag}