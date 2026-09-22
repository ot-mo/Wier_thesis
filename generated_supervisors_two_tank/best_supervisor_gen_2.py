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
        eff1.append(float(step["tank1"]["pump_effort"]))
        eff2.append(float(step["tank2"]["pump_effort"]))
        ae1.append(abs(float(step["tank1"]["error"])))
        ae2.append(abs(float(step["tank2"]["error"])))

    n = len(telemetry_window)
    k_recent = 10 if n > 10 else n
    k_wide = 40 if n > 40 else n

    # Tank-specific physical parameters: tank 1 is the source tank pumping against
    # its own gravity drain into tank 2, and tank 2 is partly fed by that drain,
    # so the two tanks do not share the same healthy effort/error band.
    CFG1 = {"eff_high": 3.0, "eff_mild": 2.4, "err_high": 0.50, "err_mild": 0.25, "err_sev": 0.80}
    CFG2 = {"eff_high": 2.8, "eff_mild": 2.2, "err_high": 0.45, "err_mild": 0.22, "err_sev": 0.75}

    def evaluate(eff, aerr, cfg, eff_bias, err_bias):
        eff_high = cfg["eff_high"] + eff_bias
        eff_mild = cfg["eff_mild"] + eff_bias
        err_mild = cfg["err_mild"] + err_bias
        err_sev = cfg["err_sev"] + err_bias

        eff_r = eff[n - k_recent:]
        err_r = aerr[n - k_recent:]
        eff_w = eff[n - k_wide:]
        err_w = aerr[n - k_wide:]

        f_eff_r = sum(1 for v in eff_r if v > eff_high) / float(k_recent)
        f_eff_w = sum(1 for v in eff_w if v > eff_high) / float(k_wide)
        f_joint_r = sum(1 for i in range(k_recent) if eff_r[i] > eff_mild and err_r[i] > err_mild) / float(k_recent)
        f_joint_w = sum(1 for i in range(k_wide) if eff_w[i] > eff_mild and err_w[i] > err_mild) / float(k_wide)
        f_sev_r = sum(1 for v in err_r if v > err_sev) / float(k_recent)
        mean_eff_w = sum(eff_w) / float(k_wide)
        mean_err_w = sum(err_w) / float(k_wide)

        reasons = []
        if f_eff_r >= 0.5:
            reasons.append("high pump effort")
        if f_eff_w >= 0.6 and mean_err_w > err_mild:
            reasons.append("effort high across window")
        if f_joint_w >= 0.5:
            reasons.append("effort + error elevated across window")
        if f_joint_r >= 0.6:
            reasons.append("effort and error jointly elevated")
        if f_sev_r >= 0.7:
            reasons.append("large persistent tracking error")
        if mean_eff_w > eff_high and mean_err_w > err_mild:
            reasons.append("mean effort and residual error above healthy band")

        anomaly = len(reasons) > 0
        calm = (mean_eff_w < eff_mild and mean_err_w < err_mild
                and f_eff_w < 0.1 and f_joint_w < 0.1)
        return anomaly, calm, reasons

    t1_anomaly, t1_calm, t1_reasons = evaluate(eff1, ae1, CFG1, 0.0, 0.0)

    # Cascade coordination: tank 1 drains into tank 2, so a tank-1 fault perturbs
    # tank 2 even when tank 2 itself is healthy. While tank 1 is flagged, tank 2
    # must supply slightly more of its own evidence before it is flagged too -
    # this removes upstream-driven false positives without blinding tank 2 to a
    # genuinely simultaneous (both-tanks) fault.
    if t1_anomaly:
        t2_anomaly, t2_calm, t2_reasons = evaluate(eff2, ae2, CFG2, 0.4, 0.06)
    else:
        t2_anomaly, t2_calm, t2_reasons = evaluate(eff2, ae2, CFG2, 0.0, 0.0)

    LOWER_STEP = 0.2
    RESTORE_STEP = 0.5

    def next_setpoint(sp, nom, anomaly, calm):
        floor = 0.7 * nom
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
        coupling = "both tanks flagged on independent evidence (tank1 cascade allowed for)"
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
