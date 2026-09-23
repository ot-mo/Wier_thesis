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
        mid = m // 2
        if m % 2 == 1:
            return s[mid]
        return 0.5 * (s[mid - 1] + s[mid])

    def mean(xs):
        if not xs:
            return 0.0
        return sum(xs) / float(len(xs))

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

    def tail(xs, k):
        if k <= 0:
            return []
        if len(xs) <= k:
            return xs
        return xs[len(xs) - k:]

    def frac_above(effs, ref, margin, win):
        seg = tail(effs, win)
        m = len(seg)
        if m <= 0:
            return 0.0
        cnt = 0
        for e in seg:
            if e > ref + margin:
                cnt += 1
        return cnt / float(m)

    base_count = min(8, n)
    if base_count < 3:
        base_count = max(1, n)

    early1 = eff1[:base_count]
    early2 = eff2[:base_count]
    early_err1 = err1[:base_count]
    early_err2 = err2[:base_count]

    ref1 = quantile(early1, 0.20)
    ref2 = quantile(early2, 0.20)

    base_err1 = mean_abs(early_err1)
    base_err2 = mean_abs(early_err2)
    med_abs_err1 = median([abs(e) for e in early_err1]) if early_err1 else 0.0
    med_abs_err2 = median([abs(e) for e in early_err2]) if early_err2 else 0.0

    def mad(xs, center):
        if not xs:
            return 0.0
        return median([abs(x - center) for x in xs])

    sigma1 = mad(early1, ref1)
    if sigma1 < 0.05:
        sigma1 = 0.05
    sigma2 = mad(early2, ref2)
    if sigma2 < 0.05:
        sigma2 = 0.05

    sigma_err1 = mad([abs(e) for e in early_err1], med_abs_err1) if early_err1 else 0.0
    if sigma_err1 < 0.05:
        sigma_err1 = 0.05
    sigma_err2 = mad([abs(e) for e in early_err2], med_abs_err2) if early_err2 else 0.0
    if sigma_err2 < 0.05:
        sigma_err2 = 0.05

    rw = min(5, n)
    wfrac = min(8, n)
    wlong = min(12, n)
    h = min(8, n)

    recent_eff1 = median(tail(eff1, rw))
    recent_eff2 = median(tail(eff2, rw))
    recent_ae1 = mean_abs(tail(err1, rw))
    recent_ae2 = mean_abs(tail(err2, rw))

    margin_eff1 = max(0.35, 2.5 * sigma1)
    margin_eff2 = max(0.25, 2.5 * sigma2)

    dev1 = recent_eff1 - ref1
    dev2 = recent_eff2 - ref2

    frac_high1 = frac_above(eff1, ref1, margin_eff1, wfrac)
    frac_high2 = frac_above(eff2, ref2, margin_eff2, wfrac)

    eff_trig1 = (dev1 > margin_eff1) or (frac_high1 >= 0.5)
    eff_trig2 = (dev2 > margin_eff2) or (frac_high2 >= 0.5)

    err_margin1 = max(0.12, 2.0 * sigma_err1)
    err_margin2 = max(0.10, 2.0 * sigma_err2)

    err_trig1 = ((recent_ae1 - base_err1) > err_margin1) and (dev1 > 0.20)
    err_trig2 = ((recent_ae2 - base_err2) > err_margin2) and (dev2 > 0.15)

    raw1 = eff_trig1 or err_trig1
    raw2 = eff_trig2 or err_trig2

    rec_eff1 = (median(tail(eff1, h)) - ref1) <= max(0.15, 1.5 * sigma1)
    rec_err1 = abs(mean_abs(tail(err1, h)) - base_err1) <= max(0.10, 1.5 * sigma_err1)
    rec1 = rec_eff1 and rec_err1

    rec_eff2 = (median(tail(eff2, h)) - ref2) <= max(0.12, 1.5 * sigma2)
    rec_err2 = abs(mean_abs(tail(err2, h)) - base_err2) <= max(0.08, 1.5 * sigma_err2)
    rec2 = rec_eff2 and rec_err2

    tank1_anom = bool(raw1 and not rec1)

    strong_dev2 = dev2 > max(0.80, 3.0 * sigma2)
    frac_strong2 = frac_above(eff2, ref2, max(0.80, 3.0 * sigma2), wlong)
    t2_independent = strong_dev2 or (frac_strong2 > 0.6 and dev2 > max(0.45, 2.0 * sigma2))
    t2_err_strong = ((recent_ae2 - base_err2) > max(0.30, 3.0 * sigma_err2)) and (dev2 > 0.50)
    t2_independent = t2_independent or t2_err_strong

    upstream1 = bool(raw1 or (dev1 > 0.35) or (recent_eff1 > ref1 + max(0.5, 2.0 * sigma1)))
    cascade = bool(upstream1 and raw2 and (not t2_independent))
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (effort/error above healthy baseline)'
    elif (dev1 < 0.20) and (recent_ae1 < base_err1 + 0.10) and (t1_sp < nom1):
        new1 = min(nom1, t1_sp + RESTORE_STEP)
        diag1 = 'tank1 stable, restoring toward nominal'
    else:
        new1 = t1_sp
        diag1 = 'tank1 nominal'

    if tank2_anom:
        cand2 = max(nom2 - MAX_DEPRESS, t2_sp - LOWER_STEP)
        if cand2 > t2_sp:
            cand2 = t2_sp
        new2 = cand2
        diag2 = 'tank2 anomaly suspected (independent, sustained evidence)'
    elif cascade:
        new2 = t2_sp
        diag2 = 'tank2 perturbation attributed to upstream tank1 fault (cascade, not flagged)'
    elif (dev2 < 0.20) and (recent_ae2 < base_err2 + 0.08) and (t2_sp < nom2):
        new2 = min(nom2, t2_sp + RESTORE_STEP)
        diag2 = 'tank2 stable, restoring toward nominal'
    else:
        new2 = t2_sp
        diag2 = 'tank2 nominal'

    return {
        'diagnosis': diag1 + '; ' + diag2,
        'adjusted_setpoints': {'tank1': new1, 'tank2': new2},
        'anomaly_flags': {'tank1': tank1_anom, 'tank2': tank2_anom},
    }
