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

    if not isinstance(telemetry_window, (list, tuple)):
        telemetry_window = []
    n = len(telemetry_window)
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
                        v = 0.0
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
        if kk <= 0:
            return []
        if len(xs) <= kk:
            return xs
        return xs[len(xs) - kk:]

    def frac_eff_only(effs, threshold, win):
        seg = tail(effs, win)
        m = len(seg)
        if m <= 0:
            return 0.0
        cnt = 0
        for e in seg:
            if e > threshold:
                cnt += 1
        return cnt / float(m)

    def linreg(xs, ys):
        m = len(xs)
        if m < 2:
            return 0.0, (ys[0] if ys else 0.0)
        mx = sum(xs) / float(m)
        my = sum(ys) / float(m)
        num = 0.0
        den = 0.0
        for i in range(m):
            dx = xs[i] - mx
            num += dx * (ys[i] - my)
            den += dx * dx
        if den <= 1e-9:
            return 0.0, my
        slope = num / den
        if slope < 0.0:
            slope = 0.0
        elif slope > 2.0:
            slope = 2.0
        resids = []
        for i in range(m):
            resids.append(ys[i] - slope * xs[i])
        intercept = median(resids)
        return slope, intercept

    early_n = max(3, min(8, max(2, int(n * 0.40))))
    if early_n > n:
        early_n = n

    q_early1 = quantile(eff1[:early_n], 0.20)
    q_full1 = quantile(eff1, 0.20)
    ref1 = min(q_early1, q_full1)
    if ref1 < 0.0:
        ref1 = 0.0

    q_early2 = quantile(eff2[:early_n], 0.20)
    q_full2 = quantile(eff2, 0.20)
    ref2 = min(q_early2, q_full2)
    if ref2 < 0.0:
        ref2 = 0.0

    Kdet = min(6, max(3, n))
    Krec = min(3, n)
    W = min(10, n)

    rm1 = median(tail(eff1, Kdet))
    ae1 = mean_abs(tail(err1, Kdet))
    dev1 = rm1 - ref1

    T1_ABS = 2.10
    T1_SPIKE = 2.70
    T1_DEV_MARGIN = 0.25
    T1_DEV_HARD = 0.45
    T1_SUST_MARGIN = 0.25
    T1_SUST_ABS = 1.75
    T1_SUST_FRAC = 0.55

    dev_anom1 = (dev1 > T1_DEV_MARGIN and ae1 > 0.05) or (dev1 > T1_DEV_HARD)
    sust_thresh1 = max(ref1 + T1_SUST_MARGIN, T1_SUST_ABS)
    sust1 = frac_eff_only(eff1, sust_thresh1, W) >= T1_SUST_FRAC
    raw1 = (rm1 > T1_ABS) or (rm1 > T1_SPIKE) or dev_anom1 or sust1

    rm2 = median(tail(eff2, Kdet))
    ae2 = mean_abs(tail(err2, Kdet))
    dev2 = rm2 - ref2

    T2_ABS = 1.85
    T2_SPIKE = 2.20
    T2_DEV_MARGIN = 0.20
    T2_DEV_HARD = 0.40
    T2_SUST_MARGIN = 0.20
    T2_SUST_ABS = 1.50
    T2_SUST_FRAC = 0.55

    dev_anom2 = (dev2 > T2_DEV_MARGIN and ae2 > 0.05) or (dev2 > T2_DEV_HARD)
    sust_thresh2 = max(ref2 + T2_SUST_MARGIN, T2_SUST_ABS)
    sust2 = frac_eff_only(eff2, sust_thresh2, W) >= T2_SUST_FRAC
    raw2_eff = (rm2 > T2_ABS) or (rm2 > T2_SPIKE) or dev_anom2 or sust2

    t2_indep = False
    resid2 = [0.0] * n
    if n >= 4:
        e1_early = eff1[:early_n]
        e2_early = eff2[:early_n]
        slope, intercept = linreg(e1_early, e2_early)
        for i in range(n):
            resid2[i] = eff2[i] - (slope * eff1[i] + intercept)
        rmed_res2 = median(tail(resid2, Kdet))
        res_sust2 = frac_eff_only(resid2, 0.30, W) >= 0.55
        t2_indep = (rmed_res2 > 0.30) or res_sust2

    raw2 = raw2_eff or t2_indep

    upstream1_active = raw1 or dev1 > 0.30 or rm1 > 1.80
    t2_coupled_only = (not t2_indep) and upstream1_active and raw2

    rec_eff1 = median(tail(eff1, Krec))
    rec_err1 = mean_abs(tail(err1, Krec))
    rec1 = (rec_eff1 <= ref1 + 0.20) and (rec_err1 <= 0.12)

    rec_eff2 = median(tail(eff2, Krec))
    rec_err2 = mean_abs(tail(err2, Krec))
    rec2 = (rec_eff2 <= ref2 + 0.20) and (rec_err2 <= 0.12)

    tank1_anom = bool(raw1 and not rec1)
    tank2_anom = bool((raw2 and not rec2) and not t2_coupled_only)

    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    rec_dev1 = median(tail(eff1, Krec)) - ref1
    rec_ae1 = mean_abs(tail(err1, Krec))

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (effort shift above early baseline)'
    else:
        tank1_anom = False
        if (rec_dev1 < 0.25) and (rec_ae1 < 0.14) and (t1_sp < nom1 - 1e-9):
            new1 = min(nom1, t1_sp + RESTORE_STEP)
            diag1 = 'tank1 stable, restoring toward nominal'
        else:
            new1 = t1_sp
            diag1 = 'tank1 nominal'

    rec_dev2 = median(tail(eff2, Krec)) - ref2
    rec_ae2 = mean_abs(tail(err2, Krec))

    if tank2_anom:
        cand2 = max(nom2 - MAX_DEPRESS, t2_sp - LOWER_STEP)
        if cand2 > t2_sp:
            cand2 = t2_sp
        new2 = cand2
        diag2 = 'tank2 anomaly suspected (independent, above cascade coupling)'
    else:
        tank2_anom = False
        if (rec_dev2 < 0.25) and (rec_ae2 < 0.14) and (t2_sp < nom2 - 1e-9):
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