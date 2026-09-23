def supervise(telemetry_window, active_setpoints, nominal_targets):
    def _num(src, key, default):
        try:
            if isinstance(src, dict):
                v = src.get(key, default)
            else:
                return float(default)
            if isinstance(v, bool):
                return float(default)
            return float(v)
        except (TypeError, ValueError):
            return float(default)

    t1_sp = _num(active_setpoints, 'tank1', 0.0)
    t2_sp = _num(active_setpoints, 'tank2', 0.0)
    nom1 = _num(nominal_targets, 'tank1', t1_sp)
    nom2 = _num(nominal_targets, 'tank2', t2_sp)

    n = len(telemetry_window) if isinstance(telemetry_window, (list, tuple)) else 0
    if n == 0:
        return {
            'diagnosis': 'no telemetry available',
            'adjusted_setpoints': {'tank1': t1_sp, 'tank2': t2_sp},
            'anomaly_flags': {'tank1': False, 'tank2': False},
        }

    def series(tank, key):
        out = []
        for step in telemetry_window:
            v = 0.0
            if isinstance(step, dict):
                d = step.get(tank)
                if isinstance(d, dict):
                    x = d.get(key, 0.0)
                    if isinstance(x, bool):
                        x = 0.0
                    elif isinstance(x, (int, float)):
                        v = float(x)
            out.append(v)
        return out

    eff1 = series('tank1', 'pump_effort')
    eff2 = series('tank2', 'pump_effort')
    err1 = series('tank1', 'error')
    err2 = series('tank2', 'error')

    def median(xs):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        if m % 2 == 1:
            return s[m // 2]
        return (s[m // 2 - 1] + s[m // 2]) / 2.0

    def mean_abs(xs):
        if not xs:
            return 0.0
        total = 0.0
        for x in xs:
            total += abs(x)
        return total / float(len(xs))

    def quantile(xs, q):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        if m == 1:
            return s[0]
        if q <= 0.0:
            return s[0]
        if q >= 1.0:
            return s[-1]
        pos = q * (m - 1)
        lo = int(pos)
        hi = lo + 1
        if hi >= m:
            return s[-1]
        frac = pos - lo
        return s[lo] * (1.0 - frac) + s[hi] * frac

    def tail(xs, kk):
        if kk <= 0 or not xs:
            return []
        if len(xs) <= kk:
            return xs
        return xs[len(xs) - kk:]

    def frac_eff_above(effs, thresh, win):
        seg = tail(effs, win)
        m = len(seg)
        if m <= 0:
            return 0.0
        cnt = 0
        for e in seg:
            if e > thresh:
                cnt += 1
        return cnt / float(m)

    def theil_sen(xs, ys):
        m = len(xs)
        if m < 2:
            return 0.0, median(ys) if ys else 0.0
        slopes = []
        for i in range(m):
            for j in range(i + 1, m):
                denom = xs[j] - xs[i]
                if abs(denom) > 1e-9:
                    slopes.append((ys[j] - ys[i]) / denom)
        if not slopes:
            return 0.0, median(ys)
        slope = median(slopes)
        intercepts = []
        for i in range(m):
            intercepts.append(ys[i] - slope * xs[i])
        intercept = median(intercepts)
        return slope, intercept

    k = min(6, n)
    w = min(8, n)
    h = min(4, n)
    base_n = min(6, n)

    def base_metrics(effs):
        seg = effs[:base_n]
        if not seg:
            return 0.0, 0.0, 0.0
        med = median(seg)
        q20 = quantile(seg, 0.20)
        base_ref = min(med, q20)
        mad = median([abs(x - med) for x in seg])
        sigma = max(mad * 1.4826, 0.0)
        return base_ref, med, sigma

    base_ref1, base_med1, sigma1 = base_metrics(eff1)
    base_ref2, base_med2, sigma2 = base_metrics(eff2)

    te1 = median(tail(eff1, k))
    te2 = median(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))

    dev_thresh1 = max(0.20, 4.0 * sigma1)
    dev_thresh2 = max(0.18, 4.0 * sigma2)

    T1_MIN_TRIGGER = 2.10
    T2_MIN_TRIGGER = 1.80
    T1_FALLBACK = 2.30
    T2_FALLBACK = 1.90

    med_thresh1 = max(base_ref1 + dev_thresh1, T1_MIN_TRIGGER)
    med_thresh2 = max(base_ref2 + dev_thresh2, T2_MIN_TRIGGER)
    sust_thresh1 = max(base_ref1 + 0.30, T1_MIN_TRIGGER)
    sust_thresh2 = max(base_ref2 + 0.26, T2_MIN_TRIGGER)

    raw1 = bool(
        (te1 > T1_FALLBACK) or
        (te1 > med_thresh1) or
        (frac_eff_above(eff1, sust_thresh1, w) >= 0.6)
    )
    raw2 = bool(
        (te2 > T2_FALLBACK) or
        (te2 > med_thresh2) or
        (frac_eff_above(eff2, sust_thresh2, w) >= 0.6)
    )

    fit_n = min(6, n)
    slope, intercept = theil_sen(eff1[:fit_n], eff2[:fit_n])
    res_list = []
    tail_start = max(0, n - k)
    for i in range(tail_start, n):
        pred = slope * eff1[i] + intercept
        res_list.append(eff2[i] - pred)
    med_res = median(res_list) if res_list else 0.0
    frac_res_high = 0.0
    if res_list:
        cnt = 0
        for r in res_list:
            if r > 0.25:
                cnt += 1
        frac_res_high = cnt / float(len(res_list))

    dev2 = te2 - base_ref2
    t2_strong = bool(
        (med_res > 0.30) or
        (te2 > 2.70 and ae2 > 0.12) or
        (te2 > 2.50 and ae2 > 0.20) or
        (dev2 > 0.70) or
        (med_res > 0.15 and frac_res_high >= 0.6)
    )

    rec1 = (median(tail(eff1, h)) <= base_ref1 + 0.20) and (mean_abs(tail(err1, h)) <= 0.15)
    rec2 = (median(tail(eff2, h)) <= base_ref2 + 0.20) and (mean_abs(tail(err2, h)) <= 0.15)

    restore_ok1 = (not raw1) and rec1
    restore_ok2 = (not raw2) and rec2

    MAX_DEPRESS = 1.0
    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8

    if raw1:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        flag1 = True
        diag1 = 'tank1 anomaly suspected'
    elif t1_sp < nom1 - 0.02:
        if restore_ok1:
            new1 = min(nom1, t1_sp + RESTORE_STEP)
            flag1 = new1 < nom1 - 0.02
            diag1 = 'tank1 recovering'
        else:
            new1 = t1_sp
            flag1 = True
            diag1 = 'tank1 fault not cleared, setpoint held'
    else:
        if t1_sp < nom1:
            new1 = min(nom1, t1_sp + RESTORE_STEP)
            flag1 = new1 < nom1 - 0.02
            diag1 = 'tank1 restoring to nominal'
        else:
            new1 = t1_sp
            flag1 = False
            diag1 = 'tank1 nominal'

    t1_active = bool(flag1 or raw1)

    if raw2 and t1_active and (not t2_strong):
        cascade2 = True
        tank2_anom = False
    else:
        cascade2 = False
        tank2_anom = bool(raw2)

    if tank2_anom:
        cand2 = max(nom2 - MAX_DEPRESS, t2_sp - LOWER_STEP)
        if cand2 > t2_sp:
            cand2 = t2_sp
        new2 = cand2
        flag2 = True
        diag2 = 'tank2 anomaly suspected'
    elif cascade2:
        new2 = t2_sp
        flag2 = False
        diag2 = 'tank2 perturbed by tank1 fault, not flagged'
    elif t2_sp < nom2 - 0.02:
        if restore_ok2:
            new2 = min(nom2, t2_sp + RESTORE_STEP)
            flag2 = new2 < nom2 - 0.02
            diag2 = 'tank2 recovering'
        else:
            new2 = t2_sp
            flag2 = True
            diag2 = 'tank2 fault not cleared, setpoint held'
    else:
        if t2_sp < nom2:
            new2 = min(nom2, t2_sp + RESTORE_STEP)
            flag2 = new2 < nom2 - 0.02
            diag2 = 'tank2 restoring to nominal'
        else:
            new2 = t2_sp
            flag2 = False
            diag2 = 'tank2 nominal'

    return {
        'diagnosis': diag1 + '; ' + diag2,
        'adjusted_setpoints': {'tank1': new1, 'tank2': new2},
        'anomaly_flags': {'tank1': flag1, 'tank2': flag2},
    }