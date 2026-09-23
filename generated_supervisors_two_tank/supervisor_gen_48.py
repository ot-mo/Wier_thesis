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

    def robust_slope_intercept(xs, ys):
        m = len(xs)
        slopes = []
        for i in range(m):
            xi = xs[i]
            yi = ys[i]
            for j in range(i + 1, m):
                dx = xs[j] - xi
                if abs(dx) > 1e-12:
                    slopes.append((ys[j] - yi) / dx)
        if not slopes:
            return 0.0, median(ys)
        slope = median(slopes)
        inter = median([y - slope * x for x, y in zip(xs, ys)])
        return slope, inter

    k = min(8, n)
    w = min(10, n)
    wlong = min(16, n)

    early_n = max(3, min(8, n // 3))
    if early_n > n:
        early_n = n
    early_eff1 = eff1[:early_n]
    early_eff2 = eff2[:early_n]
    q_early1 = quantile(early_eff1, 0.20)
    q_early2 = quantile(early_eff2, 0.20)
    q_full1 = quantile(eff1, 0.20)
    q_full2 = quantile(eff2, 0.20)
    ref1 = min(q_early1, q_full1)
    ref2 = min(q_early2, q_full2)

    te1 = median(tail(eff1, k))
    te2 = median(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))

    dev1 = te1 - ref1
    dev2 = te2 - ref2

    abs_eff1 = te1 > 2.50
    spike1 = te1 > 3.10
    dev1_anom = (dev1 > 0.50 and ae1 > 0.08) or (dev1 > 0.85)
    sustained_thresh1 = max(ref1 + 0.45, 2.05)
    sust1 = frac_eff_only(eff1, sustained_thresh1, w) >= 0.6
    raw1 = bool(abs_eff1 or spike1 or dev1_anom or sust1)

    abs_eff2 = te2 > 2.00
    spike2 = te2 > 2.70
    dev2_anom = (dev2 > 0.40 and ae2 > 0.08) or (dev2 > 0.75)
    sustained_thresh2 = max(ref2 + 0.35, 1.80)
    sust2 = frac_eff_only(eff2, sustained_thresh2, w) >= 0.6
    raw2 = bool(abs_eff2 or spike2 or dev2_anom or sust2)

    resid_strong = False
    tail_eff1_w = tail(eff1, wlong)
    tail_eff2_w = tail(eff2, wlong)
    if len(tail_eff1_w) >= 3 and len(tail_eff2_w) >= 3:
        slope, intercept = robust_slope_intercept(eff1[:early_n], eff2[:early_n])
        resids = [tail_eff2_w[i] - (intercept + slope * tail_eff1_w[i]) for i in range(len(tail_eff1_w))]
        med_resid = median(resids)
        resid_strong = med_resid > 0.60

    t2_strong = (
        (te2 > 2.80 and ae2 > 0.15) or
        (te2 > 2.40 and ae2 > 0.20) or
        (dev2 > 0.80) or
        (frac_cond(eff2, err2, 2.00, 0.15, wlong) > 0.6) or
        (frac_eff_only(eff2, 2.10, wlong) > 0.85) or
        resid_strong
    )

    h = max(3, min(5, k))
    rec1 = (median(tail(eff1, h)) <= ref1 + 0.35) and (mean_abs(tail(err1, h)) <= 0.14)
    rec2 = (median(tail(eff2, h)) <= ref2 + 0.30) and (mean_abs(tail(err2, h)) <= 0.14)

    LOWER_STEP = 0.3
    MAX_DEPRESS = 1.0
    EPS = 0.12

    t1_depressed = t1_sp < nom1 - EPS
    t1_raw_active = raw1 and not rec1
    t1_fault_active = t1_raw_active or (t1_depressed and not rec1)
    tank1_anom = t1_fault_active or t1_depressed

    if t1_fault_active:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (effort shift above early baseline)'
    elif t1_depressed and rec1:
        new1 = nom1
        diag1 = 'tank1 recovered, restoring toward nominal'
    else:
        new1 = t1_sp
        diag1 = 'tank1 nominal'

    upstream1 = tank1_anom or raw1 or dev1 > 0.40 or te1 > 2.20
    t2_depressed = t2_sp < nom2 - EPS
    cascade = upstream1 and raw2 and not t2_strong and not t2_depressed
    t2_raw_active = raw2 and not rec2
    t2_fault_active = (not cascade) and (t2_raw_active or (t2_depressed and not rec2))
    tank2_anom = t2_fault_active or t2_depressed

    if t2_fault_active:
        cand2 = max(nom2 - MAX_DEPRESS, t2_sp - LOWER_STEP)
        if cand2 > t2_sp:
            cand2 = t2_sp
        new2 = cand2
        diag2 = 'tank2 anomaly suspected (independent, above epidemic coupling)'
    elif cascade:
        new2 = t2_sp
        diag2 = 'tank2 perturbation attributed to upstream tank1 fault (cascade, not flagged)'
    elif t2_depressed and rec2:
        new2 = nom2
        diag2 = 'tank2 recovered, restoring toward nominal'
    else:
        new2 = t2_sp
        diag2 = 'tank2 nominal'

    return {
        'diagnosis': diag1 + '; ' + diag2,
        'adjusted_setpoints': {'tank1': new1, 'tank2': new2},
        'anomaly_flags': {'tank1': tank1_anom, 'tank2': tank2_anom},
    }