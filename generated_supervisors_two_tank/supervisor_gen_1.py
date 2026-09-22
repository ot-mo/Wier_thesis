def supervise(telemetry_window, active_setpoints, nominal_targets):
    setpoint1 = float(active_setpoints["tank1"])
    setpoint2 = float(active_setpoints["tank2"])
    nominal1 = float(nominal_targets["tank1"])
    nominal2 = float(nominal_targets["tank2"])

    if len(telemetry_window) == 0:
        return {
            "diagnosis": "no telemetry; holding setpoints",
            "adjusted_setpoints": {"tank1": setpoint1, "tank2": setpoint2},
            "anomaly_flags": {"tank1": False, "tank2": False},
        }

    n = len(telemetry_window)

    eff1 = [float(step["tank1"]["pump_effort"]) for step in telemetry_window]
    eff2 = [float(step["tank2"]["pump_effort"]) for step in telemetry_window]
    err1 = [abs(float(step["tank1"]["error"])) for step in telemetry_window]
    err2 = [abs(float(step["tank2"]["error"])) for step in telemetry_window]

    avg_eff1 = sum(eff1) / n
    avg_eff2 = sum(eff2) / n
    avg_err1 = sum(err1) / n
    avg_err2 = sum(err2) / n
    max_err1 = max(err1)
    max_err2 = max(err2)
    latest_err1 = err1[-1]
    latest_err2 = err2[-1]

    # trend over the window: last third vs first third of |error|
    k = max(1, n // 3)
    trend1 = sum(err1[-k:]) / k - sum(err1[:k]) / k
    trend2 = sum(err2[-k:]) / k - sum(err2[:k]) / k

    # ---- per-tank physical tuning (tank2 runs on a smaller pump/level scale) ----
    T1_EFF_HIGH = 2.4
    T1_EFF_LOW = 1.4
    T1_ERR_HIGH = 0.20
    T1_ERR_LOW = 0.10
    T2_EFF_HIGH = 1.7
    T2_EFF_LOW = 0.9
    T2_ERR_HIGH = 0.14
    T2_ERR_LOW = 0.07
    TREND_MARGIN = 0.04

    def _tank_anomaly(avg_eff, avg_err, max_err, latest_err, trend,
                      eff_high, err_high, err_low):
        # any single fault signature is enough, instead of AND-gating two gates
        persistent_error = avg_err > err_high
        controller_strain = avg_eff > eff_high and (avg_err > err_low or latest_err > err_high)
        worsening = trend > TREND_MARGIN and latest_err > err_low
        large_excursion = max_err > err_high * 2.0 and avg_err > err_low
        return persistent_error or controller_strain or worsening or large_excursion

    tank1_anomaly = _tank_anomaly(avg_eff1, avg_err1, max_err1, latest_err1, trend1,
                                  T1_EFF_HIGH, T1_ERR_HIGH, T1_ERR_LOW)

    # Tank2 is downstream of tank1: a tank1 fault starves tank2's inflow and makes
    # tank2's controller strain even when tank2 is perfectly healthy. That is a
    # DISTURBANCE to tank2, not a tank2 fault, so tank1 evidence must NOT flag
    # tank2 on its own (the old hard OR produced tank2 false positives). Cascade
    # coupling is instead expressed as a small tolerance bump so tank1-induced
    # strain is not misread as a tank2 fault.
    if tank1_anomaly:
        tank2_anomaly = _tank_anomaly(avg_eff2, avg_err2, max_err2, latest_err2, trend2,
                                      T2_EFF_HIGH * 1.15, T2_ERR_HIGH * 1.25, T2_ERR_LOW * 1.2)
    else:
        tank2_anomaly = _tank_anomaly(avg_eff2, avg_err2, max_err2, latest_err2, trend2,
                                      T2_EFF_HIGH, T2_ERR_HIGH, T2_ERR_LOW)

    LOWER_STEP = 0.5
    MAX_DEVIATION = 2.0

    if tank1_anomaly:
        new_setpoint1 = max(nominal1 - MAX_DEVIATION, setpoint1 - LOWER_STEP)
        diag1 = "tank1 anomaly (sustained high effort/error)"
    elif setpoint1 < nominal1 - 1e-9 and avg_eff1 < T1_EFF_LOW and avg_err1 < T1_ERR_LOW:
        # fully restore as soon as tank1's own fault has cleared
        new_setpoint1 = nominal1
        diag1 = "tank1 recovered, restoring to nominal"
    else:
        new_setpoint1 = setpoint1
        diag1 = "tank1 nominal"

    if tank2_anomaly:
        new_setpoint2 = max(nominal2 - MAX_DEVIATION, setpoint2 - LOWER_STEP)
        diag2 = "tank2 anomaly (sustained high effort/error)"
    elif setpoint2 < nominal2 - 1e-9 and avg_eff2 < T2_EFF_LOW and avg_err2 < T2_ERR_LOW:
        # fully restore as soon as tank2's own fault has cleared
        new_setpoint2 = nominal2
        diag2 = "tank2 recovered, restoring to nominal"
    else:
        new_setpoint2 = setpoint2
        diag2 = "tank2 nominal"

    if tank1_anomaly and not tank2_anomaly:
        diag2 = diag2 + " (tank1 fault treated as cascade disturbance, not a tank2 fault)"

    return {
        "diagnosis": diag1 + "; " + diag2,
        "adjusted_setpoints": {"tank1": new_setpoint1, "tank2": new_setpoint2},
        "anomaly_flags": {"tank1": tank1_anomaly, "tank2": tank2_anomaly},
    }
