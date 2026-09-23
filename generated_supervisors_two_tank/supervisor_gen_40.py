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

    sp1 = _num(active_setpoints, 'tank1', 0.0)
    sp2 = _num(active_setpoints, 'tank2', 0.0)
    nom1 = _num(nominal_targets, 'tank1', sp1)
    nom2 = _num(nominal_targets, 'tank2', sp2)

    n = len(telemetry_window) if isinstance(telemetry_window, (list, tuple)) else 0
    if n == 0:
        return {
            'diagnosis': 'no telemetry available',
            'adjusted_setpoints': {'tank1': sp1, 'tank2': sp2},
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

    def median(xs):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        if m % 2 == 1:
            return s[m // 2]
        return (s[m // 2 - 1] + s[m // 2]) / 2.0

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

    k_det = min(5, n)
    w = min(12, n)
    wlong = min(16, n)
    rec_h = min(8, n)

    recent_eff1 = median(tail(eff1, k_det))
    recent_eff2 = median(tail(eff2, k_det))
    recent_err1 = mean_abs(tail(err1, k_det))
    recent_err2 = mean_abs(tail(err2, k_det))

    early_n = min(20, n)
    if early_n >= 3:
        ref1_early = quantile(eff1[:early_n], 0.25)
        ref2_early = quantile(eff2[:early_n], 0.25)
    else:
        ref1_early = quantile(eff1, 0.20)
        ref2_early = quantile(eff2, 0.20)
    ref1_whole = quantile(eff1, 0.20)
    ref2_whole = quantile(eff2, 0.20)
    ref1 = (ref1_early + ref1_whole) * 0.5
    ref2 = (ref2_early + ref2_whole) * 0.5

    dev1 = recent_eff1 - ref1
    dev2 = recent_eff2 - ref2

    def frac_high(effs, ref, margin, win):
        seg = tail(effs, win)
        m = len(seg)
        if m <= 0:
            return 0.0
        cnt = 0
        for x in seg:
            if x > ref + margin:
                cnt += 1
        return cnt / float(m)

    sus_frac1 = frac_high(eff1, ref1, 0.45, w)
    sus_frac2 = frac_high(eff2, ref2, 0.35, w)

    raw1 = ((dev1 > 0.45 and recent_err1 > 0.16) or
            (dev1 > 0.75) or
            (sus_frac1 >= 0.55) or
            (recent_eff1 > 2.8 and recent_err1 > 0.16) or
            (recent_eff1 > 3.2 and recent_err1 > 0.20))

    raw2 = ((dev2 > 0.40 and recent_err2 > 0.18) or
            (dev2 > 0.70) or
            (sus_frac2 >= 0.55) or
            (recent_eff2 > 2.2 and recent_err2 > 0.18) or
            (recent_eff2 > 2.7 and recent_err2 > 0.20))

    t2_long = frac_high(eff2, ref2, 0.45, wlong) > 0.6

    depressed1 = sp1 < nom1 - 0.05
    depressed2 = sp2 < nom2 - 0.05

    rec_eff1 = mean(tail(eff1, rec_h)) <= ref1 + 0.35
    rec_err1 = mean_abs(tail(err1, rec_h)) <= 0.16
    restore_ready1 = rec_eff1 and rec_err1

    rec_eff2 = mean(tail(eff2, rec_h)) <= ref2 + 0.30
    rec_err2 = mean_abs(tail(err2, rec_h)) <= 0.14
    restore_ready2 = rec_eff2 and rec_err2

    recovered1 = (not raw1) and (not depressed1) and restore_ready1
    tank1_anom = bool((raw1 or depressed1) and not recovered1)

    upstream1_active = raw1 or (dev1 > 0.50) or (recent_eff1 > 2.4)
    strong2 = ((dev2 > 0.55) or
               (recent_eff2 > 2.6 and recent_err2 > 0.20) or
               t2_long or
               (sus_frac2 >= 0.70) or
               (recent_eff2 > 2.9 and recent_err2 > 0.30))
    cascade = upstream1_active and raw2 and (not strong2)

    recovered2 = (not raw2) and (not depressed2) and restore_ready2 and (sp2 >= nom2 - 0.05)
    tank2_anom = bool(((raw2 and (not cascade)) or depressed2) and (not recovered2))

    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    if raw1:
        cand1 = max(nom1 - MAX_DEPRESS, sp1 - LOWER_STEP)
        if cand1 > sp1:
            cand1 = sp1
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (sustained effort/error above healthy reference)'
    elif (depressed1 and restore_ready1) or (sp1 < nom1 and restore_ready1):
        new1 = min(nom1, sp1 + RESTORE_STEP)
        diag1 = 'tank1 recovering, restoring toward nominal'
    else:
        new1 = sp1
        diag1 = 'tank1 nominal'

    if raw2 and (not cascade):
        cand2 = max(nom2 - MAX_DEPRESS, sp2 - LOWER_STEP)
        if cand2 > sp2:
            cand2 = sp2
        new2 = cand2
        diag2 = 'tank2 anomaly suspected (independent, sustained evidence)'
    elif cascade:
        new2 = sp2
        diag2 = 'tank2 perturbation attributed to upstream tank1 fault (cascade, not flagged)'
    elif (depressed2 and restore_ready2) or (sp2 < nom2 and restore_ready2):
        new2 = min(nom2, sp2 + RESTORE_STEP)
        diag2 = 'tank2 recovering, restoring toward nominal'
    else:
        new2 = sp2
        diag2 = 'tank2 nominal'

    return {
        'diagnosis': diag1 + '; ' + diag2,
        'adjusted_setpoints': {'tank1': new1, 'tank2': new2},
        'anomaly_flags': {'tank1': tank1_anom, 'tank2': tank2_anom},
    }
