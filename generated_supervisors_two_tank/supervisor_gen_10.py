def supervise(telemetry_window, active_setpoints, nominal_targets):
    tank1_setpoint = float(active_setpoints["tank1"])
    tank2_setpoint = float(active_setpoints["tank2"])
    nominal1 = float(nominal_targets["tank1"])
    nominal2 = float(nominal_targets["tank2"])

    def hold(msg):
        return {
            "diagnosis": msg,
            "adjusted_setpoints": {"tank1": tank1_setpoint, "tank2": tank2_setpoint},
            "anomaly_flags": {"tank1": False, "tank2": False},
        }

    if not telemetry_window:
        return hold("no telemetry: holding current setpoints")

    eff1 = []
    err1 = []
    eff2 = []
    err2 = []
    for step in telemetry_window:
        if not isinstance(step, dict):
            continue
        t1 = step.get("tank1")
        t2 = step.get("tank2")
        if isinstance(t1, dict):
            eff1.append(abs(float(t1.get("pump_effort", 0.0))))
            err1.append(abs(float(t1.get("error", 0.0))))
        if isinstance(t2, dict):
            eff2.append(abs(float(t2.get("pump_effort", 0.0))))
            err2.append(abs(float(t2.get("error", 0.0))))

    if not eff1 or not eff2:
        return hold("no usable telemetry: holding current setpoints")

    # Per-tank detector. Margins are derived from the tank's OWN window spread
    # so a tank that legitimately runs hot (tank2, fed by tank1's gravity drain)
    # is compared against itself rather than against a global absolute band.
    def detect(eff, err, scale):
        m = len(eff)
        kr = 10 if m >= 10 else m
        kw = 40 if m >= 40 else m

        r_eff = eff[m - kr:]
        r_err = err[m - kr:]
        w_eff = eff[m - kw:]
        w_err = err[m - kw:]

        m_r_eff = sum(r_eff) / float(kr)
        m_r_err = sum(r_err) / float(kr)

        min_eff = min(w_eff)
        min_err = min(w_err)
        mean_w_eff = sum(w_eff) / float(kw)
        mean_w_err = sum(w_err) / float(kw)

        err_spread = mean_w_err - min_err
        if err_spread < 0.0:
            err_spread = 0.0
        eff_spread = mean_w_eff - min_eff
        if eff_spread < 0.0:
            eff_spread = 0.0

        err_margin = (0.22 if 0.9 * err_spread < 0.22 else 0.9 * err_spread) * scale
        eff_floor = 0.6 if 1.0 * eff_spread < 0.6 else 1.0 * eff_spread
        eff_margin = eff_floor * scale

        err_gap = m_r_err - min_err
        eff_gap = m_r_eff - min_eff

        reasons = []
        if err_gap > err_margin:
            reasons.append("tracking error above its own quiet floor")
        if eff_gap > eff_margin and err_gap > 0.4 * err_margin:
            reasons.append("pump effort above its quiet floor with residual error")
        if eff_gap > 1.8 * eff_margin:
            reasons.append("pump effort far above its quiet floor")
        if m_r_err > 0.6 * scale:
            reasons.append("large absolute tracking error")

        severe = 0.0
        for v in w_err:
            if v > 1.0 * scale:
                severe += 1.0
        severe_frac = severe / float(kw)
        if severe_frac >= 0.4:
            reasons.append("severe error sustained across window")

        anomaly = len(reasons) > 0
        calm = (err_gap < 0.5 * err_margin
                and eff_gap < 0.5 * eff_margin
                and m_r_err < 0.45 * scale)
        return anomaly, calm, reasons

    t1_anomaly, t1_calm, t1_reasons = detect(eff1, err1, 1.0)

    # Cascade: tank1 drains into tank2, so a tank1 fault perturbs tank2's inflow
    # even when tank2 is healthy. While tank1 is flagged, tank2 must clear ~40%
    # more evidence before it is flagged too.
    if t1_anomaly:
        t2_anomaly, t2_calm, t2_reasons = detect(eff2, err2, 1.4)
    else:
        t2_anomaly, t2_calm, t2_reasons = detect(eff2, err2, 1.0)

    LOWER_STEP = 0.2
    RESTORE_STEP = 0.5

    def next_setpoint(sp, nom, anomaly):
        floor = 0.7 * nom
        if floor < 0.0:
            floor = 0.0
        if anomaly:
            new_sp = sp - LOWER_STEP
            if new_sp < floor:
                new_sp = floor
            return new_sp
        if sp > nom:
            return nom
        if sp < nom:
            new_sp = sp + RESTORE_STEP
            if new_sp > nom:
                new_sp = nom
            return new_sp
        return sp

    new_setpoint1 = next_setpoint(tank1_setpoint, nominal1, t1_anomaly)
    new_setpoint2 = next_setpoint(tank2_setpoint, nominal2, t2_anomaly)

    if t1_anomaly:
        diag1 = "tank1 anomaly (" + ", ".join(t1_reasons) + "); demand backed off"
    elif tank1_setpoint != nominal1:
        diag1 = "tank1 healthy; restoring toward nominal"
    else:
        diag1 = "tank1 nominal"

    if t2_anomaly:
        diag2 = "tank2 anomaly (" + ", ".join(t2_reasons) + "); demand backed off"
    elif tank2_setpoint != nominal2:
        diag2 = "tank2 healthy; restoring toward nominal"
    else:
        diag2 = "tank2 nominal"

    if t1_anomaly and t2_anomaly:
        coupling = "both tanks flagged on independent evidence"
    elif t1_anomaly:
        coupling = "tank1 fault present; tank2 perturbation treated as upstream-driven, not flagged"
    elif t2_anomaly:
        coupling = "tank2 fault present with tank1 healthy (no upstream contribution)"
    else:
        coupling = "no cross-tank coupling active"

    return {
        "diagnosis": diag1 + "; " + diag2 + "; " + coupling,
        "adjusted_setpoints": {"tank1": new_setpoint1, "tank2": new_setpoint2},
        "anomaly_flags": {"tank1": t1_anomaly, "tank2": t2_anomaly},
    }
