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

    def mean(xs):
        if not xs:
            return 0.0
        return sum(xs) / float(len(xs))

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
        return sum(abs(x) for x in xs) / float(len(xs))

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

    def frac_cond(effs, errs, cond_eff, cond_err, win):
        seg_e = tail(effs, win)
        seg_r = tail(errs, win)
        m = len(seg_e)
        if m <= 0:
            return 0.0
        cnt = 0
        for i in range(m):
            if seg_e[i] > cond_eff and abs(seg_r[i]) > cond_err:
                cnt += 1
        return cnt / float(m)

    def frac_over(xs, threshold, win):
        seg = tail(xs, win)
        m = len(seg)
        if m <= 0:
            return 0.0
        cnt = 0
        for x in seg:
            if x > threshold:
                cnt += 1
        return cnt / float(m)

    early_n = max(3, min(8, n))
    early1 = eff1[:early_n]
    early2 = eff2[:early_n]
    base1 = min(quantile(early1, 0.10), quantile(early1, 0.20))
    base2 = min(quantile(early2, 0.10), quantile(early2, 0.20))

    k_t1 = min(8, n)
    if t1_sp < nom1 - 0.05:
        k_t1 = max(3, min(5, n))
    k_t2 = min(8, n)
    if t2_sp < nom2 - 0.05:
        k_t2 = max(3, min(5, n))

    te1 = median(tail(eff1, k_t1))
    te2 = median(tail(eff2, k_t2))
    ae1 = mean_abs(tail(err1, k_t1))
    ae2 = mean_abs(tail(err2, k_t2))

    dev1 = te1 - base1
    dev2 = te2 - base2

    abs_eff1 = te1 > 1.85
    spike1 = te1 > 2.60
    dev1_anom = (dev1 > 0.35 and ae1 > 0.06) or (dev1 > 0.70)
    sustained_thresh1 = max(base1 + 0.30, 1.75)
    sust1 = frac_eff_only(eff1, sustained_thresh1, min(10, n)) >= 0.55
    raw1 = bool(abs_eff1 or spike1 or dev1_anom or sust1)

    abs_eff2 = te2 > 1.65
    spike2 = te2 > 2.30
    dev2_anom = (dev2 > 0.30 and ae2 > 0.06) or (dev2 > 0.65)
    sustained_thresh2 = max(base2 + 0.30, 1.60)
    sust2 = frac_eff_only(eff2, sustained_thresh2, min(10, n)) >= 0.55
    raw2 = bool(abs_eff2 or spike2 or dev2_anom or sust2)

    h1 = max(3, min(5, k_t1))
    h2 = max(3, min(5, k_t2))
    rec1 = (median(tail(eff1, h1)) <= base1 + 0.35) and (mean_abs(tail(err1, h1)) <= 0.14)
    rec2 = (median(tail(eff2, h2)) <= base2 + 0.30) and (mean_abs(tail(err2, h2)) <= 0.14)

    fit_n = max(4, min(10, n))
    if fit_n > n:
        fit_n = n
    xs_fit = eff1[:fit_n]
    ys_fit = eff2[:fit_n]

    def robust_linreg(xs, ys):
        m = len(xs)
        if m < 2:
            return 0.0, (ys[0] if m == 1 else 0.0)
        slopes = []
        for i in range(m):
            for j in range(i + 1, m):
                if abs(xs[j] - xs[i]) > 1e-9:
                    slopes.append((ys[j] - ys[i]) / (xs[j] - xs[i]))
        if not slopes:
            slope = 0.0
        else:
            slope = median(slopes)
        intercepts = [ys[i] - slope * xs[i] for i in range(m)]
        intercept = median(intercepts)
        return slope, intercept

    slope, intercept = robust_linreg(xs_fit, ys_fit)
    residual2 = []
    for i in range(n):
        residual2.append(eff2[i] - (intercept + slope * eff1[i]))

    res_med = median(tail(residual2, k_t2))
    res_frac_high = frac_over(residual2, 0.25, k_t2)
    res_abs_fit = median([abs(x) for x in residual2[:fit_n]])
    resid_thresh = max(0.25, 2.0 * res_abs_fit)
    t2_indep_resid = (
        (res_med > resid_thresh and res_frac_high >= 0.45) or
        (res_med > 0.40 and res_frac_high >= 0.55) or
        (res_med > 0.60)
    )

    wlong = min(16, n)
    t2_strong = (
        (te2 > 2.80 and ae2 > 0.15) or
        (te2 > 2.40 and ae2 > 0.20) or
        (dev2 > 0.80) or
        (frac_cond(eff2, err2, 2.00, 0.15, wlong) > 0.6) or
        (frac_eff_only(eff2, 2.10, wlong) > 0.85) or
        bool(t2_indep_resid)
    )

    upstream1 = bool(raw1 or dev1 > 0.35 or te1 > 1.80)
    cascade = bool(upstream1 and raw2 and (not t2_strong))

    tank1_anom = bool(raw1 and not rec1)
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 1.0
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (effort shift above baseline)'
    elif (dev1 < 0.35) and (ae1 < 0.14) and (t1_sp < nom1):
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
        diag2 = 'tank2 anomaly suspected (independent of upstream coupling)'
    elif cascade:
        new2 = t2_sp
        diag2 = 'tank2 perturbation attributed to upstream tank1 fault (cascade, not flagged)'
    elif (dev2 < 0.30) and (ae2 < 0.14) and (t2_sp < nom2):
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