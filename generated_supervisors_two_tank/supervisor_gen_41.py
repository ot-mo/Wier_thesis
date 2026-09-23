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

    def median(xs):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        mid = m // 2
        if m % 2:
            return s[mid]
        return (s[mid - 1] + s[mid]) / 2.0

    def median_abs(xs):
        if not xs:
            return 0.0
        return median([abs(x) for x in xs])

    def tail(xs, kk):
        if kk <= 0:
            return []
        if len(xs) <= kk:
            return xs
        return xs[len(xs) - kk:]

    def frac_above(effs, ref, margin, win):
        seg = tail(effs, win)
        if not seg:
            return 0.0
        cnt = 0
        for e in seg:
            if e > ref + margin:
                cnt += 1
        return cnt / float(len(seg))

    def frac_above_abs(effs, bar, win):
        seg = tail(effs, win)
        if not seg:
            return 0.0
        cnt = 0
        for e in seg:
            if e > bar:
                cnt += 1
        return cnt / float(len(seg))

    def frac_healthy(effs, ref, margin, win):
        seg = tail(effs, win)
        if not seg:
            return 0.0
        cnt = 0
        for e in seg:
            if e <= ref + margin:
                cnt += 1
        return cnt / float(len(seg))

    k = min(8, n)
    w = min(12, n)
    wlong = min(18, n)
    h = max(3, min(4, k))

    early_n = min(8, n)
    ref1 = min(quantile(eff1, 0.20), quantile(eff1[:early_n], 0.35))
    ref2 = min(quantile(eff2, 0.20), quantile(eff2[:early_n], 0.35))

    med1 = median(tail(eff1, k))
    med2 = median(tail(eff2, k))
    mae1 = median_abs(tail(err1, k))
    mae2 = median_abs(tail(err2, k))
    dev1 = med1 - ref1
    dev2 = med2 - ref2

    # Sustained, error-free effort-shift fractions (lessons learned: median/recent fraction detects compensated leaks)
    fs1 = frac_above(eff1, ref1, 0.25, w)
    fs2 = frac_above(eff2, ref2, 0.25, w)

    tank1_sust = (fs1 >= 0.55) and (med1 > ref1 + 0.15)
    tank2_sust = (fs2 >= 0.55) and (med2 > ref2 + 0.15)

    tank1_dev = (dev1 > 0.28) and (mae1 > 0.08)
    tank2_dev = (dev2 > 0.25) and (mae2 > 0.10)

    tank1_spike = (med1 > 1.8) and (mae1 > 0.16)
    tank2_spike = (med2 > 1.6) and (mae2 > 0.22)

    tank1_rel = (dev1 > 0.55) and (mae1 > 0.08)
    tank2_rel = (dev2 > 0.50) and (mae2 > 0.10)

    raw1 = tank1_sust or tank1_dev or tank1_spike or tank1_rel
    raw2 = tank2_sust or tank2_dev or tank2_spike or tank2_rel

    # Independent tank2 evidence, above the coupling expected from tank1.
    t2_long_abs = frac_above_abs(eff2, 2.0, wlong) > 0.65
    t2_spike_indep = (med2 > 2.0) and (mae2 > 0.16)
    t2_rel_indep = (frac_above(eff2, ref2, 0.55, wlong) > 0.70) and (med2 > ref2 + 0.55) and (med2 > 1.7)
    t2_indep = t2_long_abs or t2_spike_indep or t2_rel_indep

    # Recovery uses early-window-anchored reference and requires sustained healthy effort.
    rec1 = (k >= 4) and (median(tail(eff1, h)) <= ref1 + 0.20) and (median_abs(tail(err1, h)) <= 0.10) and (frac_healthy(eff1, ref1, 0.20, w) >= 0.60)
    rec2 = (k >= 4) and (median(tail(eff2, h)) <= ref2 + 0.20) and (median_abs(tail(err2, h)) <= 0.12) and (frac_healthy(eff2, ref2, 0.20, w) >= 0.60)

    upstream1 = raw1 or (med1 > ref1 + 0.35) or (med1 > 1.7)
    cascade = upstream1 and raw2 and (not t2_indep)

    tank1_anom = bool(raw1 and not rec1)
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (sustained effort/error above healthy reference)'
    elif (dev1 < 0.30) and (mae1 < 0.10) and (t1_sp < nom1):
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
    elif (dev2 < 0.30) and (mae2 < 0.12) and (t2_sp < nom2):
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