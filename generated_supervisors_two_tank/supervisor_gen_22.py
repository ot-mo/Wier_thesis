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

    def median(xs):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        if m % 2 == 1:
            return float(s[m // 2])
        return (s[m // 2 - 1] + s[m // 2]) / 2.0

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

    def tail(xs, kk):
        if kk <= 0:
            return []
        if len(xs) <= kk:
            return xs
        return xs[len(xs) - kk:]

    def frac_rel(effs, errs, ref, eff_margin, err_margin, win):
        seg_e = tail(effs, win)
        seg_r = tail(errs, win)
        m = len(seg_e)
        if m <= 0:
            return 0.0
        bar = ref + eff_margin
        cnt = 0
        for i in range(m):
            if seg_e[i] > bar and abs(seg_r[i]) > err_margin:
                cnt += 1
        return cnt / float(m)

    def frac_abs(effs, errs, eff_bar, err_bar, win):
        seg_e = tail(effs, win)
        seg_r = tail(errs, win)
        m = len(seg_e)
        if m <= 0:
            return 0.0
        cnt = 0
        for i in range(m):
            if seg_e[i] > eff_bar and abs(seg_r[i]) > err_bar:
                cnt += 1
        return cnt / float(m)

    def err_threshold(errs):
        early = errs[:min(6, len(errs))]
        if len(early) < 3:
            return 0.16
        med = median(early)
        mad = median([abs(e - med) for e in early])
        thr = 0.08 + 2.0 * mad
        return max(0.08, min(0.20, thr))

    k = min(6, n)
    w = min(10, n)
    wlong = min(16, n)
    h = max(3, k // 2)
    if h > k:
        h = k

    early_n = min(5, n)
    if early_n >= 3:
        ref1 = median(eff1[:early_n])
        ref2 = median(eff2[:early_n])
    else:
        ref1 = quantile(eff1, 0.25)
        ref2 = quantile(eff2, 0.25)
    q25_1 = quantile(eff1, 0.25)
    q25_2 = quantile(eff2, 0.25)
    if q25_1 < ref1:
        ref1 = q25_1
    if q25_2 < ref2:
        ref2 = q25_2

    err_thr1 = err_threshold(err1)
    err_thr2 = err_threshold(err2)

    te1 = mean(tail(eff1, k))
    te2 = mean(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))

    dev1 = te1 - ref1
    dev2 = te2 - ref2

    spike1 = (te1 > 3.0) and (ae1 > max(0.10, err_thr1 * 0.7))
    abs1 = (te1 > 2.2) and (ae1 > err_thr1)
    dev_hi1 = (dev1 > 0.30) and (ae1 > err_thr1 * 0.9)
    last1 = (len(eff1) > 0 and eff1[-1] > ref1 + 0.60 and abs(err1[-1]) > err_thr1 * 0.8)
    sust1 = frac_rel(eff1, err1, ref1, 0.25, err_thr1 * 0.8, w) >= 0.5
    raw1 = bool(spike1 or abs1 or dev_hi1 or sust1 or last1)

    spike2 = (te2 > 2.6) and (ae2 > max(0.18, err_thr2 * 0.7))
    abs2 = (te2 > 1.7) and (ae2 > err_thr2)
    dev_hi2 = (dev2 > 0.28) and (ae2 > err_thr2 * 0.9)
    last2 = (len(eff2) > 0 and eff2[-1] > ref2 + 0.55 and abs(err2[-1]) > err_thr2 * 0.8)
    sust2 = frac_rel(eff2, err2, ref2, 0.25, err_thr2 * 0.8, w) >= 0.5
    raw2 = bool(spike2 or abs2 or dev_hi2 or sust2 or last2)

    t2_long = frac_abs(eff2, err2, 1.7, max(0.15, err_thr2), wlong) > 0.5

    rec1 = (k >= 4) and (mean(tail(eff1, h)) <= ref1 + 0.40) and (mean_abs(tail(err1, h)) <= max(0.20, 1.5 * err_thr1))
    rec2 = (k >= 4) and (mean(tail(eff2, h)) <= ref2 + 0.35) and (mean_abs(tail(err2, h)) <= max(0.20, 1.5 * err_thr2))

    tank1_anom = bool(raw1 and not rec1)

    upstream1_active = bool(raw1 or dev1 > 0.30 or te1 > 2.0)
    strong_tank2 = bool(spike2 or t2_long)
    cascade = bool(upstream1_active and raw2 and (not strong_tank2))
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 1.0
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (sustained effort/error above healthy reference)'
    elif (dev1 < 0.25) and (ae1 < max(0.18, 1.2 * err_thr1)) and (t1_sp < nom1):
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
    elif (dev2 < 0.25) and (ae2 < max(0.18, 1.2 * err_thr2)) and (t2_sp < nom2):
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
