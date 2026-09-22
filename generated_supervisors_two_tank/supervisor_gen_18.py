def supervise(telemetry_window, active_setpoints, nominal_targets):
    tank1_sp = float(active_setpoints["tank1"])
    tank2_sp = float(active_setpoints["tank2"])
    nom1 = float(nominal_targets["tank1"])
    nom2 = float(nominal_targets["tank2"])

    n = len(telemetry_window)
    if n == 0:
        return {
            "diagnosis": "no telemetry available",
            "adjusted_setpoints": {"tank1": tank1_sp, "tank2": tank2_sp},
            "anomaly_flags": {"tank1": False, "tank2": False},
        }

    def col(tank, key):
        out = []
        for step in telemetry_window:
            v = 0.0
            if isinstance(step, dict):
                d = step.get(tank)
                if isinstance(d, dict):
                    x = d.get(key, 0.0)
                    if isinstance(x, bool):
                        x = 0.0
                    if isinstance(x, (int, float)):
                        v = float(x)
            out.append(v)
        return out

    eff1 = col("tank1", "pump_effort")
    eff2 = col("tank2", "pump_effort")
    err1 = col("tank1", "error")
    err2 = col("tank2", "error")

    def mean(xs):
        if not xs:
            return 0.0
        return sum(xs) / float(len(xs))

    def mean_abs(xs):
        if not xs:
            return 0.0
        t = 0.0
        for x in xs:
            t += abs(x)
        return t / float(len(xs))

    def quantile(xs, p):
        if not xs:
            return 0.0
        s = sorted(xs)
        m = len(s)
        if m == 1:
            return s[0]
        pos = p * (m - 1)
        lo = int(pos)
        hi = lo + 1
        if hi >= m:
            return s[-1]
        f = pos - lo
        return s[lo] * (1.0 - f) + s[hi] * f

    def frac_above(xs, w, thr):
        m = len(xs)
        if m == 0:
            return 0.0
        start = m - w if m > w else 0
        cnt = 0
        tot = 0
        for i in range(start, m):
            tot += 1
            if xs[i] > thr:
                cnt += 1
        return cnt / float(tot) if tot else 0.0

    def frac_pair(effs, errs, w, ebar, errbar):
        m = len(effs)
        if m == 0:
            return 0.0
        start = m - w if m > w else 0
        cnt = 0
        tot = 0
        for i in range(start, m):
            tot += 1
            if effs[i] > ebar and abs(errs[i]) > errbar:
                cnt += 1
        return cnt / float(tot) if tot else 0.0

    kr = min(5, n)
    km = min(14, n)
    k10 = min(10, n)

    ref1 = quantile(eff1, 0.2)
    ref2 = quantile(eff2, 0.2)
    eps1 = max(ref1, 0.5)
    eps2 = max(ref2, 0.5)

    r_e1 = mean(eff1[-kr:])
    r_e2 = mean(eff2[-kr:])
    r_a1 = mean_abs(err1[-kr:])
    r_a2 = mean_abs(err2[-kr:])

    m_e1 = mean(eff1[-km:])
    m_e2 = mean(eff2[-km:])
    m_a1 = mean_abs(err1[-km:])
    m_a2 = mean_abs(err2[-km:])
    m_b1 = mean(err1[-km:])
    m_b2 = mean(err2[-km:])

    te1 = mean(eff1[-k10:])
    te2 = mean(eff2[-k10:])
    ae1 = mean_abs(err1[-k10:])
    ae2 = mean_abs(err2[-k10:])

    rise1 = m_e1 - ref1
    rise2 = m_e2 - ref2

    frac1 = frac_above(eff1, km, ref1 + 0.45)
    frac2 = frac_above(eff2, km, ref2 + 0.40)
    sus1 = frac_pair(eff1, err1, km, ref1 + 0.55, 0.10)
    sus2 = frac_pair(eff2, err2, km, ref2 + 0.45, 0.10)

    # ---- Tank 1: high-head source, larger absolute effort scale ----
    t1_ceil = (r_e1 > 3.3) or (m_e1 > 3.0)
    t1_rise = (rise1 > 0.45) and (rise1 > 0.25 * eps1) and ((m_a1 > 0.12) or (abs(m_b1) > 0.15))
    t1_eff = (rise1 > 0.90) and (rise1 > 0.50 * eps1) and (frac1 > 0.50)
    t1_sus = (frac1 > 0.70) and (m_a1 > 0.10)
    t1_sus2 = sus1 > 0.75
    raw1 = t1_ceil or t1_rise or t1_eff or t1_sus or t1_sus2

    # ---- Tank 2: gravity-fed, lower baseline, weaker absolute signal ----
    t2_ceil = ((r_e2 > 2.7) and (r_a2 > 0.22)) or ((m_e2 > 2.3) and (m_a2 > 0.20))
    t2_rise = (rise2 > 0.35) and (rise2 > 0.22 * eps2) and ((m_a2 > 0.13) or (abs(m_b2) > 0.15))
    t2_eff = (rise2 > 0.80) and (rise2 > 0.50 * eps2) and (frac2 > 0.50)
    t2_sus = (frac2 > 0.70) and (m_a2 > 0.10)
    t2_sus2 = sus2 > 0.75
    raw2 = t2_ceil or t2_rise or t2_eff or t2_sus or t2_sus2

    # ---- recovery: effort genuinely back at the calm reference ----
    def recovered(errs, effs, ref, cap, errcap):
        if len(effs) < 5:
            return False
        h = min(6, len(effs))
        re = mean(effs[-h:])
        ra = mean_abs(errs[-h:])
        if re > cap:
            return False
        if ra > errcap:
            return False
        return re <= ref + 0.25

    rec1 = recovered(err1, eff1, ref1, 2.5, 0.13)
    rec2 = recovered(err2, eff2, ref2, 1.9, 0.10)

    warm = n >= 3

    tank1_anom = bool(warm and raw1 and not rec1)

    # ---- cascade: tank1 hydraulically feeds tank2 ----
    # Only an unambiguously strong, organised tank2 signature escapes
    # attribution to the upstream tank1 fault (this preserves zero tank2
    # false positives in tank1-only fault scenarios).
    strong2 = bool(((te2 > 2.9) and (ae2 > 0.32)) or
                   (frac_pair(eff2, err2, min(16, n), 2.0, 0.20) > 0.70))
    cascade = bool(tank1_anom and raw2 and (not strong2))
    tank2_anom = bool(warm and raw2 and not rec2 and not cascade)

    LOWER_STEP = 0.3
    RESTORE_STEP = 0.8
    MAX_DEPRESS = 1.0

    if tank1_anom:
        new1 = max(nom1 - MAX_DEPRESS, tank1_sp - LOWER_STEP)
        diag1 = "tank1 anomaly: effort sustained above its calm baseline"
    elif tank1_sp < nom1 - 1e-9:
        new1 = min(nom1, tank1_sp + RESTORE_STEP)
        diag1 = "tank1 recovered, restoring setpoint toward nominal"
    else:
        new1 = tank1_sp
        diag1 = "tank1 nominal"

    if tank2_anom:
        new2 = max(nom2 - MAX_DEPRESS, tank2_sp - LOWER_STEP)
        diag2 = "tank2 anomaly: independent evidence beyond upstream coupling"
    elif cascade:
        new2 = tank2_sp
        diag2 = "tank2 perturbation attributed to upstream tank1 fault"
    elif tank2_sp < nom2 - 1e-9:
        new2 = min(nom2, tank2_sp + RESTORE_STEP)
        diag2 = "tank2 recovered, restoring setpoint toward nominal"
    else:
        new2 = tank2_sp
        diag2 = "tank2 nominal"

    return {
        "diagnosis": diag1 + "; " + diag2,
        "adjusted_setpoints": {"tank1": new1, "tank2": new2},
        "anomaly_flags": {"tank1": tank1_anom, "tank2": tank2_anom},
    }