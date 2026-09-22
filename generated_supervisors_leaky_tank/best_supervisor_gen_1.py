def supervise(telemetry_window, active_setpoint, nominal_target):
    avg_effort = sum(step["pump_effort"] for step in telemetry_window) / len(telemetry_window)
    avg_abs_error = sum(abs(step["error"]) for step in telemetry_window) / len(telemetry_window)
    latest_error = abs(telemetry_window[-1]["error"])

    EFFORT_HIGH = 2.5
    EFFORT_LOW = 1.5
    ERROR_HIGH = 0.5
    ERROR_LOW = 0.2
    LOWER_STEP = 0.5
    RESTORE_STEP = 0.25

    if avg_effort > EFFORT_HIGH and avg_abs_error > ERROR_HIGH:
        adjusted_setpoint = active_setpoint - LOWER_STEP
        diagnosis = "Anomaly suspected: high pump effort with sustained tracking error, lowering setpoint."
        anomaly_flag = True
    elif avg_effort < EFFORT_LOW and avg_abs_error < ERROR_LOW and active_setpoint < nominal_target:
        adjusted_setpoint = min(nominal_target, active_setpoint + RESTORE_STEP)
        diagnosis = "System stable and below nominal, restoring setpoint toward nominal."
        anomaly_flag = False
    else:
        adjusted_setpoint = active_setpoint
        diagnosis = "Nominal operation, no action."
        anomaly_flag = False

    return {
        "diagnosis": diagnosis,
        "adjusted_setpoint": adjusted_setpoint,
        "anomaly_flag": anomaly_flag,
    }