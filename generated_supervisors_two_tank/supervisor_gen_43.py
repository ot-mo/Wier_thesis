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

    healthy_cap1 = 1.9
    healthy_cap2 = 1.55

    early_n = max(5, min(10, n // 3))
    if early_n > n:
        early_n = n
    early_eff1 = eff1[:early_n]
    early_eff2 = eff2[:early_n]
    q_early1 = quantile(early_eff1, 0.20)
    q_early2 = quantile(early_eff2, 0.20)
    q_full1 = quantile(eff1, 0.20)
    q_full2 = quantile(eff2, 0.20)
    ref1 = min(q_early1, q_full1, healthy_cap1)
    ref2 = min(q_early2, q_full2, healthy_cap2)

    k = min(8, n)
    w = min(10, n)
    wlong = min(16, n)
    h = max(3, min(5, k))

    te1 = median(tail(eff1, k))
    te2 = median(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))
    dev1 = te1 - ref1
    dev2 = te2 - ref2

    abs_eff1 = te1 > 2.40
    spike1 = te1 > 3.00
    dev1_anom = (dev1 > 0.45 and ae1 > 0.08) or (dev1 > 0.75)
    sustained_thresh1 = max(ref1 + 0.40, 1.90)
    sust1 = frac_eff_only(eff1, sustained_thresh1, w) >= 0.6
    raw1 = bool(abs_eff1 or spike1 or dev1_anom or sust1)

    rec_short1 = (median(tail(eff1, h)) <= ref1 + 0.35) and (mean_abs(tail(err1, h)) <= 0.14)
    rec_long1 = (median(tail(eff1, wlong)) <= ref1 + 0.30) and (mean_abs(tail(err1, wlong)) <= 0.14)
    depressed1 = t1_sp < nom1 - 0.01
    if depressed1:
        tank1_anom = bool(raw1 or not rec_long1)
    else:
        tank1_anom = bool(raw1 and not rec_short1)

    n_early = len(early_eff1)
    if n_early >= 3:
        mx = sum(early_eff1) / n_early
        my = sum(early_eff2) / n_early
        var_x = sum((xi - mx) ** 2 for xi in early_eff1) / n_early
        cov_xy = sum((early_eff1[i] - mx) * (early_eff2[i] - my) for i in range(n_early)) / n_early
        beta = cov_xy / var_x if var_x > 1e-6 else 0.0
        alpha = median(early_eff2) - beta * median(early_eff1)
    else:
        beta = 0.0
        alpha = ref2

    tail_eff1_w = tail(eff1, wlong)
    tail_eff2_w = tail(eff2, wlong)
    residuals = []
    mw = min(len(tail_eff1_w), len(tail_eff2_w))
    for i in range(mw):
        expected = alpha + beta * tail_eff1_w[i]
        residuals.append(tail_eff2_w[i] - expected)
    med_res = median(residuals) if residuals else 0.0
    frac_res = (sum(1 for r in residuals if r > 0.70) / float(len(residuals))) if residuals else 0.0
    residual_raw2 = bool((med_res > 0.80 and frac_res > 0.6) or (med_res > 1.00 and frac_res > 0.4))

    abs_eff2 = te2 > 2.00
    spike2 = te2 > 2.60
    dev2_anom = (dev2 > 0.40 and ae2 > 0.08) or (dev2 > 0.65)
    sustained_thresh2 = max(ref2 + 0.35, 1.70)
    sust2 = frac_eff_only(eff2, sustained_thresh2, w) >= 0.6
    raw2_eff = bool(abs_eff2 or spike2 or dev2_anom or sust2)
    raw2 = bool(raw2_eff or residual_raw2)

    rec_short2 = (median(tail(eff2, h)) <= ref2 + 0.30) and (mean_abs(tail(err2, h)) <= 0.14)
    rec_long2 = (median(tail(eff2, wlong)) <= ref2 + 0.25) and (mean_abs(tail(err2, wlong)) <= 0.14)
    if residuals:
        rec_long2 = rec_long2 and (median(residuals[-min(wlong, len(residuals)):]) <= 0.35)
    depressed2 = t2_sp < nom2 - 0.01

    t2_strong = (
        (te2 > 2.50 and ae2 > 0.12) or
        (te2 > 2.20 and ae2 > 0.18) or
        (dev2 > 0.65) or
        (frac_cond(eff2, err2, 1.80, 0.12, wlong) > 0.6) or
        (frac_eff_only(eff2, 2.00, wlong) > 0.80) or
        residual_raw2
    )

    upstream1 = bool(raw1 or dev1 > 0.35 or te1 > 2.00)
    cascade = bool(upstream1 and raw2 and (not t2_strong))

    if depressed2:
        tank2_anom = bool(raw2 or not rec_long2)
    else:
        tank2_anom = bool(raw2 and not rec_short2 and not cascade)

    lower_step = 0.25
    restore_step = 0.5
    max_depress = 1.0

    if tank1_anom:
        cand1 = max(nom1 - max_depress, t1_sp - lower_step)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected'
    elif t1_sp < nom1 and rec_long1:
        new1 = min(nom1, t1_sp + restore_step)
        diag1 = 'tank1 stable, restoring toward nominal'
    else:
        new1 = t1_sp
        diag1 = 'tank1 nominal'

    if tank2_anom:
        cand2 = max(nom2 - max_depress, t2_sp - lower_step)
        if cand2 > t2_sp:
            cand2 = t2_sp
        new2 = cand2
        diag2 = 'tank2 anomaly suspected'
    elif cascade:
        new2 = t2_sp
        diag2 = 'tank2 perturbation attributed to tank1 fault (cascade, not flagged)'
    elif t2_sp < nom2 and rec_long2:
        new2 = min(nom2, t2_sp + restore_step)
        diag2 = 'tank2 stable, restoring toward nominal'
    else:
        new2 = t2_sp
        diag2 = 'tank2 nominal'

    return {
        'diagnosis': diag1 + '; ' + diag2,
        'adjusted_setpoints': {'tank1': new1, 'tank2': new2},
        'anomaly_flags': {'tank1': tank1_anom, 'tank2': tank2_anom},
    }