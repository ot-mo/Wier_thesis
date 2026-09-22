def supervise(telemetry_window, active_setpoints, nominal_targets):
    efforts1 = [step["tank1"]["pump_effort"] for step in telemetry_window]
    efforts2 = [step["tank2"]["pump_effort"] for step in telemetry_window]
    errors1 = [step["tank1"]["error"] for step in telemetry_window]
    errors2 = [step["tank2"]["error"] for step in telemetry_window]

    avg_effort1 = sum(efforts1) / len(efforts1)
    avg_effort2 = sum(efforts2) / len(efforts2)
    latest_error1 = abs(errors1[-1])
    latest_error2 = abs(errors2[-1])
    avg_abs_error1 = sum(abs(e) for e in errors1) / len(errors1)
    avg_abs_error2 = sum(abs(e) for e in errors2) / len(errors2)

    EFFORT_HIGH = 3.5
    EFFORT_LOW = 2.0
    ERROR_HIGH = 0.5
    ERROR_LOW = 0.15
    LOWER_STEP = 0.5
    RESTORE_STEP = 0.25

    tank1_setpoint = active_setpoints["tank1"]
    tank2_setpoint = active_setpoints["tank2"]
    nominal1 = nominal_targets["tank1"]
    nominal2 = nominal_targets["tank2"]

    tank1_anomaly = avg_effort1 > EFFORT_HIGH and latest_error1 > ERROR_HIGH
    tank2_local_anomaly = avg_effort2 > EFFORT_HIGH and latest_error2 > ERROR_HIGH

    # Tank1's gravity outflow feeds tank2, so a tank1 fault is an early-warning
    # for tank2 even before tank2's own effort/error crosses its own threshold -
    # this is the one piece of genuine coordination in an otherwise per-tank
    # heuristic, left simple deliberately so the offline learner has room to
    # discover better cross-tank coordination on top of it.
    tank2_anomaly = tank2_local_anomaly or tank1_anomaly

    if tank1_anomaly:
        new_setpoint1 = tank1_setpoint - LOWER_STEP
        diag1 = "tank1 anomaly suspected (high effort + growing error)"
    elif avg_effort1 < EFFORT_LOW and avg_abs_error1 < ERROR_LOW and tank1_setpoint < nominal1:
        new_setpoint1 = min(nominal1, tank1_setpoint + RESTORE_STEP)
        diag1 = "tank1 stable, restoring toward nominal"
    else:
        new_setpoint1 = tank1_setpoint
        diag1 = "tank1 nominal"

    if tank2_anomaly:
        new_setpoint2 = tank2_setpoint - LOWER_STEP
        diag2 = "tank2 anomaly suspected (local or upstream tank1 fault)"
    elif avg_effort2 < EFFORT_LOW and avg_abs_error2 < ERROR_LOW and tank2_setpoint < nominal2:
        new_setpoint2 = min(nominal2, tank2_setpoint + RESTORE_STEP)
        diag2 = "tank2 stable, restoring toward nominal"
    else:
        new_setpoint2 = tank2_setpoint
        diag2 = "tank2 nominal"

    return {
        "diagnosis": f"{diag1}; {diag2}",
        "adjusted_setpoints": {"tank1": new_setpoint1, "tank2": new_setpoint2},
        "anomaly_flags": {"tank1": tank1_anomaly, "tank2": tank2_anomaly},
    }
