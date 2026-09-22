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

    def recent_abs_mean(series):
        total = 0.0
        for x in series[-k:]:
            total += abs(x)
        return total / k

    se1 = recent_mean(efforts1)
    se2 = recent_mean(efforts2)
    ae1 = recent_abs_mean(errors1)
    ae2 = recent_abs_mean(errors2)

    # Per-tank detection thresholds. Tank2 is gravity-fed with a lower
    # pump-effort baseline, so its thresholds stay below tank1's.
    E1_HI = 2.6
    R1_HI = 0.28
    E1_STRONG = 3.6

    E2_HI = 2.1
    R2_HI = 0.22
    E2_STRONG = 3.2
    R2_STRONG = 0.42

    # Stable / restore thresholds (a quiet tank can be walked back to nominal).
    EFF_STABLE = 2.0
    ERR_STABLE = 0.15

    # tank1: either moderate effort with elevated error, or unambiguous strong effort.
    tank1_anom = (se1 > E1_HI and ae1 > R1_HI) or se1 > E1_STRONG

    # tank2: same two-path shape, but the strong path still needs BOTH effort and
    # error so that a tank1-induced inflow perturbation cannot impersonate a fault.
    tank2_evidence = se2 > E2_HI and ae2 > R2_HI
    tank2_strong = se2 > E2_STRONG and ae2 > R2_STRONG
    # Cascade coordination: only flag tank2 when tank1 is quiet (trust the
    # sensitive detector) or when tank2's own evidence is strong.
    tank2_anom = tank2_evidence and (tank2_strong or not tank1_anom)

    LOWER_STEP = 0.35
    RESTORE_STEP = 1.0
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
