def supervise(telemetry_window, active_setpoints, nominal_targets):
    tank1_sp = active_setpoints["tank1"]
    tank2_sp = active_setpoints["tank2"]
    nom1 = nominal_targets["tank1"]
    nom2 = nominal_targets["tank2"]

    n = len(telemetry_window)
    if n == 0:
        return {
            "diagnosis": "no telemetry available",
            "adjusted_setpoints": {"tank1": tank1_sp, "tank2": tank2_sp},
            "anomaly_flags": {"tank1": False, "tank2": False},
        }

    efforts1 = [step["tank1"]["pump_effort"] for step in telemetry_window]
    efforts2 = [step["tank2"]["pump_effort"] for step in telemetry_window]
    errors1 = [step["tank1"]["error"] for step in telemetry_window]
    errors2 = [step["tank2"]["error"] for step in telemetry_window]

    k = min(6, n)

    def recent_mean(series):
        return sum(series[-k:]) / k

    se1 = recent_mean(efforts1)
    se2 = recent_mean(efforts2)
    ae1 = recent_mean([abs(x) for x in errors1])
    ae2 = recent_mean([abs(x) for x in errors2])

    # Per-tank detection thresholds. The two tanks have different physical
    # parameters: tank2 is gravity-fed and its pump-effort baseline is lower,
    # so its thresholds must be lower than tank1's.
    E1_HI = 3.0
    R1_HI = 0.35
    E1_STRONG = 4.5

    E2_HI = 2.4
    R2_HI = 0.25
    # When tank1 is faulting, only unambiguous tank2 evidence is trusted,
    # because a tank1 leak perturbs tank2's inflow and can mimic a tank2 fault.
    E2_STRONG = 3.4
    R2_STRONG = 0.45

    tank1_anom = (se1 > E1_HI and ae1 > R1_HI) or se1 > E1_STRONG

    tank2_evidence = se2 > E2_HI and ae2 > R2_HI
    tank2_strong = se2 > E2_STRONG and ae2 > R2_STRONG
    # Cascade coordination: do not blindly propagate tank1's fault to tank2.
    # Only flag tank2 when either tank1 is quiet (trust the sensitive detector)
    # or tank2's evidence is strong enough to be its own fault.
    tank2_anom = tank2_evidence and (tank2_strong or not tank1_anom)

    EFF_STABLE = 2.0
    ERR_STABLE = 0.15
    LOWER_STEP = 0.35
    RESTORE_STEP = 0.5
    MAX_DEPRESS = 1.0

    if tank1_anom:
        new1 = max(nom1 - MAX_DEPRESS, tank1_sp - LOWER_STEP)
        diag1 = "tank1 anomaly suspected (high pump effort with elevated error)"
    elif se1 < EFF_STABLE and ae1 < ERR_STABLE and tank1_sp < nom1:
        new1 = min(nom1, tank1_sp + RESTORE_STEP)
        diag1 = "tank1 stable, restoring toward nominal"
    else:
        new1 = tank1_sp
        diag1 = "tank1 nominal"

    if tank2_anom:
        new2 = max(nom2 - MAX_DEPRESS, tank2_sp - LOWER_STEP)
        diag2 = "tank2 anomaly suspected (own strong evidence)"
    elif tank2_evidence and tank1_anom:
        new2 = tank2_sp
        diag2 = "tank2 perturbation attributed to upstream tank1 fault"
    elif se2 < EFF_STABLE and ae2 < ERR_STABLE and tank2_sp < nom2:
        new2 = min(nom2, tank2_sp + RESTORE_STEP)
        diag2 = "tank2 stable, restoring toward nominal"
    else:
        new2 = tank2_sp
        diag2 = "tank2 nominal"

    return {
        "diagnosis": diag1 + "; " + diag2,
        "adjusted_setpoints": {"tank1": new1, "tank2": new2},
        "anomaly_flags": {"tank1": tank1_anom, "tank2": tank2_anom},
    }
