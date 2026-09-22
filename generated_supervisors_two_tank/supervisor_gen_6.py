def supervise(telemetry_window, active_setpoints, nominal_targets):
    tank1_setpoint = float(active_setpoints["tank1"])
    tank2_setpoint = float(active_setpoints["tank2"])
    nominal1 = float(nominal_targets["tank1"])
    nominal2 = float(nominal_targets["tank2"])

    if not telemetry_window:
        return {
            "diagnosis": "no telemetry: holding current setpoints",
            "adjusted_setpoints": {"tank1": tank1_setpoint, "tank2": tank2_setpoint},
            "anomaly_flags": {"tank1": False, "tank2": False},
        }

    eff1 = []
    err1 = []
    eff2 = []
    err2 = []
    for step in telemetry_window:
        s1 = step["tank1"]
        s2 = step["tank2"]
        eff1.append(float(s1["pump_effort"]))
        err1.append(abs(float(s1["error"])))
        eff2.append(float(s2["pump_effort"]))
        err2.append(abs(float(s2["error"])))

    n = len(telemetry_window)

    def avg(vals):
        total = 0.0
        for v in vals:
            total += v
        return total / float(len(vals))

    def lowest_smoothed(vals, k):
        # lowest mean over any contiguous k-window: a robust 'healthy' anchor,
        # because faults only ever push effort/error up.
        m = len(vals)
        if m <= 0:
            return 0.0
        if k > m:
            k = m
        running = 0.0
        best = None
        for i in range(m):
            running += vals[i]
            if i >= k:
                running -= vals[i - k]
            if i + 1 >= k:
                cur = running / float(k)
                if best is None or cur < best:
                    best = cur
        if best is None:
            return avg(vals)
        return best

    def noise_sd(vals):
        # robust per-step noise scale from the median absolute first difference
        m = len(vals)
        if m < 3:
            return 0.0
        diffs = []
        for i in range(1, m):
            d = vals[i] - vals[i - 1]
            if d < 0.0:
                d = -d
            diffs.append(d)
        diffs = sorted(diffs)
        mid = diffs[len(diffs) // 2]
        return mid / 0.9539

    kr = 12 if n > 12 else n

    CFG1 = {
        "eff_margin": 0.30, "eff_frac": 0.22, "eff_sigma": 2.0,
        "err_margin": 0.14, "err_frac": 0.45, "err_sigma": 1.8,
        "abs_err": 0.78, "abs_frac": 0.65,
    }
    CFG2 = {
        "eff_margin": 0.32, "eff_frac": 0.24, "eff_sigma": 2.1,
        "err_margin": 0.15, "err_frac": 0.48, "err_sigma": 1.9,
        "abs_err": 0.82, "abs_frac": 0.65,
    }

    def analyze(eff, aerr, cfg, strict):
        rec_eff = eff[n - kr:]
        rec_err = aerr[n - kr:]

        m_eff_r = avg(rec_eff)
        m_err_r = avg(rec_err)

        ref_eff = lowest_smoothed(eff, 5)
        ref_err = lowest_smoothed(aerr, 5)
        sd_eff = noise_sd(eff)
        sd_err = noise_sd(aerr)

        eff_ex = m_eff_r - ref_eff
        if eff_ex < 0.0:
            eff_ex = 0.0
        err_ex = m_err_r - ref_err
        if err_ex < 0.0:
            err_ex = 0.0

        base_eff = cfg["eff_margin"]
        if cfg["eff_frac"] * ref_eff > base_eff:
            base_eff = cfg["eff_frac"] * ref_eff
        if cfg["eff_sigma"] * sd_eff > base_eff:
            base_eff = cfg["eff_sigma"] * sd_eff
        eff_gate = base_eff * strict

        base_err = cfg["err_margin"]
        if cfg["err_frac"] * ref_err > base_err:
            base_err = cfg["err_frac"] * ref_err
        if cfg["err_sigma"] * sd_err > base_err:
            base_err = cfg["err_sigma"] * sd_err
        err_gate = base_err * strict

        eff_up = eff_ex > eff_gate
        err_up = err_ex > err_gate
        eff_strong = eff_ex > 2.0 * eff_gate
        err_strong = err_ex > 2.0 * err_gate

        abs_err = cfg["abs_err"] * (0.5 + 0.5 * strict)
        sev = 0
        for v in rec_err:
            if v > abs_err:
                sev += 1
        sev_frac = sev / float(kr)
        abs_sev = sev_frac >= cfg["abs_frac"] and m_err_r > abs_err

        reasons = []
        if eff_up and err_up:
            reasons.append("effort and tracking error both above this tank's own healthy band")
        if eff_strong:
            reasons.append("pump effort far above this tank's own healthy band")
        if err_strong:
            reasons.append("tracking error far above this tank's own healthy band")
        if abs_sev:
            reasons.append("absolute tracking error far above the healthy band")

        anomaly = (eff_up and err_up) or eff_strong or err_strong or abs_sev
        calm = (not anomaly) and (eff_ex < 0.5 * eff_gate) and (err_ex < 0.5 * err_gate)
        return anomaly, calm, reasons

    t1_anomaly, t1_calm, t1_reasons = analyze(eff1, err1, CFG1, 1.0)

    # Cascade: tank 1 drains into tank 2, so an upstream fault perturbs tank 2.
    # While tank 1 is flagged, tank 2 must clear a raised multiplicative margin on
    # its own evidence (its disturbance is partly absorbed by its PID), but the
    # margin is only 1.25x so a genuine simultaneous tank-2 fault is still caught.
    if t1_anomaly:
        t2_strict = 1.25
    else:
        t2_strict = 1.0
    t2_anomaly, t2_calm, t2_reasons = analyze(eff2, err2, CFG2, t2_strict)

    def next_setpoint(sp, nom, anomaly, calm):
        floor_level = 0.7 * nom
        if floor_level < 0.0:
            floor_level = 0.0
        if anomaly:
            v = sp - 0.2
            if v < floor_level:
                v = floor_level
            return v
        if calm and sp < nom:
            v = sp + 0.35
            if v > nom:
                v = nom
            return v
        return sp

    new_setpoint1 = next_setpoint(tank1_setpoint, nominal1, t1_anomaly, t1_calm)
    new_setpoint2 = next_setpoint(tank2_setpoint, nominal2, t2_anomaly, t2_calm)

    if t1_anomaly:
        diag1 = "tank1 anomaly (" + ", ".join(t1_reasons) + "); demand backed off"
    elif t1_calm and tank1_setpoint < nominal1:
        diag1 = "tank1 back inside its own healthy band; restoring toward nominal"
    else:
        diag1 = "tank1 nominal"

    if t2_anomaly:
        diag2 = "tank2 anomaly (" + ", ".join(t2_reasons) + "); demand backed off"
    elif t2_calm and tank2_setpoint < nominal2:
        diag2 = "tank2 back inside its own healthy band; restoring toward nominal"
    else:
        diag2 = "tank2 nominal"

    if t1_anomaly and t2_anomaly:
        coupling = "both tanks show independent evidence of their own fault"
    elif t1_anomaly:
        coupling = "tank1 faulty; tank2 disturbance attributed to the upstream cascade unless it clears the raised margin"
    elif t2_anomaly:
        coupling = "tank2 faulty with tank1 healthy (no upstream contribution)"
    else:
        coupling = "no cross-tank coupling active"

    return {
        "diagnosis": diag1 + "; " + diag2 + "; " + coupling,
        "adjusted_setpoints": {"tank1": new_setpoint1, "tank2": new_setpoint2},
        "anomaly_flags": {"tank1": t1_anomaly, "tank2": t2_anomaly},
    }
