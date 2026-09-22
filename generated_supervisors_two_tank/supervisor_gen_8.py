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
    eff2 = []
    ae1 = []
    ae2 = []
    for step in telemetry_window:
        eff1.append(abs(float(step["tank1"]["pump_effort"])))
        eff2.append(abs(float(step["tank2"]["pump_effort"])))
        ae1.append(abs(float(step["tank1"]["error"])))
        ae2.append(abs(float(step["tank2"]["error"])))

    n = len(telemetry_window)
    k_recent = 12 if n > 12 else n
    k_wide = 40 if n > 40 else n

    # Tank-specific healthy operating bands. Tank 1 is the upstream source tank
    # pumping against its own gravity drain; tank 2 is partly fed by that drain,
    # so its nominal effort and residual tracking error sit higher and closer to
    # the band edge, and it therefore needs a wider margin before being flagged.
    CFG1 = {"eff_high": 3.05, "eff_mild": 2.55, "err_high": 0.55, "err_mild": 0.32}
    CFG2 = {"eff_high": 3.20, "eff_mild": 2.75, "err_high": 0.60, "err_mild": 0.38}

    def frac_above(seq, thr):
        c = 0
        for v in seq:
            if v > thr:
                c += 1
        return c / float(len(seq))

    def evaluate(eff, aerr, cfg, eff_bias, err_bias):
        eff_high = cfg["eff_high"] + eff_bias
        eff_mild = cfg["eff_mild"] + eff_bias
        err_high = cfg["err_high"] + err_bias
        err_mild = cfg["err_mild"] + err_bias

        eff_r = eff[n - k_recent:]
        err_r = aerr[n - k_recent:]
        eff_w = eff[n - k_wide:]
        err_w = aerr[n - k_wide:]

        f_eff_hi_r = frac_above(eff_r, eff_high)
        f_err_hi_r = frac_above(err_r, err_high)
        f_eff_hi_w = frac_above(eff_w, eff_high)
        f_err_hi_w = frac_above(err_w, err_high)
        f_eff_mild_w = frac_above(eff_w, eff_mild)
        f_err_mild_w = frac_above(err_w, err_mild)

        mean_eff_w = sum(eff_w) / float(len(eff_w))
        mean_err_w = sum(err_w) / float(len(err_w))

        reasons = []
        if f_eff_hi_r >= 0.55:
            reasons.append("recent pump effort above healthy band")
        if f_err_hi_r >= 0.50:
            reasons.append("recent tracking error above healthy band")
        if f_eff_hi_w >= 0.45 and mean_err_w > err_mild:
            reasons.append("sustained high effort with residual error")
        if f_err_hi_w >= 0.40:
            reasons.append("sustained tracking error across window")
        if mean_eff_w > eff_high and mean_err_w > err_high:
            reasons.append("mean effort and error above healthy band")

        anomaly = len(reasons) > 0

        # 'Genuinely calm' is a wide-window property: a setpoint move must not
        # briefly mask a real fault and trigger a restore / re-flag limit cycle.
        calm = (mean_eff_w < eff_mild and mean_err_w < err_mild
                and f_eff_hi_w <= 0.05 and f_err_hi_w <= 0.05
                and f_eff_mild_w <= 0.10 and f_err_mild_w <= 0.10)
        return anomaly, calm, reasons

    t1_anomaly, t1_calm, t1_reasons = evaluate(eff1, ae1, CFG1, 0.0, 0.0)

    # Cascade coordination: the tank-1 drain is part of tank-2's inflow, so a
    # tank-1 fault perturbs tank-2 even when tank-2 is healthy. While tank 1 is
    # flagged we require a modestly stronger, sustained tank-2 signature before
    # flagging tank 2 too. The margin is deliberately small so a genuine
    # simultaneous (both-tanks) fault is still detected.
    if t1_anomaly:
        t2_anomaly, t2_calm, t2_reasons = evaluate(eff2, ae2, CFG2, 0.15, 0.02)
    else:
        t2_anomaly, t2_calm, t2_reasons = evaluate(eff2, ae2, CFG2, 0.0, 0.0)

    LOWER_STEP = 0.12
    RESTORE_STEP = 0.25

    def next_setpoint(sp, nom, anomaly, calm):
        floor = 0.8 * nom
        if floor < 0.0:
            floor = 0.0
        if anomaly:
            new_sp = sp - LOWER_STEP
            if new_sp < floor:
                new_sp = floor
            return new_sp
        if calm and sp < nom:
            new_sp = sp + RESTORE_STEP
            if new_sp > nom:
                new_sp = nom
            return new_sp
        return sp

    new_setpoint1 = next_setpoint(tank1_setpoint, nominal1, t1_anomaly, t1_calm)
    new_setpoint2 = next_setpoint(tank2_setpoint, nominal2, t2_anomaly, t2_calm)

    if t1_anomaly:
        diag1 = "tank1 anomaly (" + ", ".join(t1_reasons) + "); demand backed off"
    elif t1_calm and tank1_setpoint < nominal1:
        diag1 = "tank1 healthy; restoring toward nominal"
    else:
        diag1 = "tank1 nominal"

    if t2_anomaly:
        diag2 = "tank2 anomaly (" + ", ".join(t2_reasons) + "); demand backed off"
    elif t2_calm and tank2_setpoint < nominal2:
        diag2 = "tank2 healthy; restoring toward nominal"
    else:
        diag2 = "tank2 nominal"

    if t1_anomaly and t2_anomaly:
        coupling = "both tanks flagged: tank2 signature exceeds the upstream-coupled band"
    elif t1_anomaly:
        coupling = "tank1 fault present; tank2 disturbance treated as upstream, not flagged"
    elif t2_anomaly:
        coupling = "tank2 fault present with tank1 healthy (no upstream contribution)"
    else:
        coupling = "no cross-tank coupling active"

    return {
        "diagnosis": diag1 + "; " + diag2 + "; " + coupling,
        "adjusted_setpoints": {"tank1": new_setpoint1, "tank2": new_setpoint2},
        "anomaly_flags": {"tank1": t1_anomaly, "tank2": t2_anomaly},
    }
