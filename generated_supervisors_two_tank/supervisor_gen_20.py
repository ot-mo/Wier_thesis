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

    def frac_over(xs, bar, win):
        seg = tail(xs, win)
        m = len(seg)
        if m <= 0:
            return 0.0
        cnt = 0
        for x in seg:
            if x > bar:
                cnt += 1
        return cnt / float(m)

    def frac_pair(effs, errs, eff_bar, err_bar, win):
        se = tail(effs, win)
        sr = tail(errs, win)
        m = len(se)
        if m <= 0:
            return 0.0
        cnt = 0
        for i in range(m):
            if se[i] > eff_bar and abs(sr[i]) > err_bar:
                cnt += 1
        return cnt / float(m)

    k = min(8, n)
    w = min(10, n)
    wmed = min(12, n)
    wlong = min(16, n)

    te1 = mean(tail(eff1, k))
    te2 = mean(tail(eff2, k))
    ae1 = mean_abs(tail(err1, k))
    ae2 = mean_abs(tail(err2, k))

    # Contamination-resistant healthy reference: a low quantile stays near the
    # pre-fault operating point even when a persistent fault fills most of the
    # window (an early mean would be dragged up to fault level).
    ref1 = quantile(eff1, 0.20)
    ref2 = quantile(eff2, 0.20)

    dev1 = te1 - ref1
    dev2 = te2 - ref2

    # Persistence of elevated effort relative to the healthy reference. This is
    # deliberately ERROR-INDEPENDENT: a leak/actuator fault leaves effort high
    # while the tank's own PID nulls the steady-state error.
    M1 = 0.32
    M2 = 0.26
    obs1 = frac_over(eff1, ref1 + M1, wlong)
    obs2 = frac_over(eff2, ref2 + M2, wlong)

    # ---- Tank 1: high-head source, larger absolute effort scale ----
    spike1 = (te1 > 3.4) and (ae1 > 0.22)
    abs1 = (te1 > 2.5) and (ae1 > 0.18)
    dev1_hi = (dev1 > 0.55) and (ae1 > 0.14)
    sust1 = frac_pair(eff1, err1, ref1 + 0.45, 0.14, w) >= 0.5
    eff_sust1 = (obs1 >= 0.60) and (dev1 > 0.20)
    raw1 = spike1 or abs1 or dev1_hi or sust1 or eff_sust1

    # ---- Tank 2: gravity-fed, lower baseline, softer absolute scale ----
    spike2 = (te2 > 2.9) and (ae2 > 0.28)
    abs2 = (te2 > 2.0) and (ae2 > 0.18)
    dev2_hi = (dev2 > 0.45) and (ae2 > 0.12)
    sust2 = frac_pair(eff2, err2, ref2 + 0.40, 0.12, w) >= 0.5
    eff_sust2 = (obs2 >= 0.60) and (dev2 > 0.16)
    raw2 = spike2 or abs2 or dev2_hi or sust2 or eff_sust2

    # ---- Recovery: recent effort must sit essentially AT the healthy
    # reference for most of a window. Margins are strictly tighter than the
    # detection margins, so a persistent plateau is never called recovered,
    # while a genuinely cleared fault restores cleanly.
    rec1 = (frac_over(eff1, ref1 + 0.20, wmed) <= 0.20) and (ae1 <= 0.14)
    rec2 = (frac_over(eff2, ref2 + 0.16, wmed) <= 0.20) and (ae2 <= 0.12)

    tank1_anom = bool(raw1 and not rec1)

    # ---- Cascade coupling: tank1 hydraulically feeds tank2, so an upstream
    # fault perturbs tank2 even when tank2 is healthy. Suppress tank2's own
    # evidence while upstream tank1 is active UNLESS tank2 shows a large, long
    # DEVIATION-INDEPENDENT signature of its own (a real simultaneous fault).
    upstream1 = bool(raw1 or (dev1 > 0.35) or (te1 > 2.2))
    strong2 = bool(spike2 or ((obs2 >= 0.80) and (dev2 > 0.55)))
    cascade = bool(upstream1 and raw2 and (not strong2))
    tank2_anom = bool(raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    if tank1_anom:
        cand1 = max(nom1 - MAX_DEPRESS, t1_sp - LOWER_STEP)
        if cand1 > t1_sp:
            cand1 = t1_sp
        new1 = cand1
        diag1 = 'tank1 anomaly suspected (sustained effort above healthy reference)'
    elif (dev1 < 0.30) and (ae1 < 0.16) and (t1_sp < nom1):
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
    elif (dev2 < 0.30) and (ae2 < 0.16) and (t2_sp < nom2):
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
