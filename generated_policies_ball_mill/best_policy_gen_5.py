def llm_policy(obs):
    if not hasattr(llm_policy, "sump_integral"):
        llm_policy.sump_integral = 0.0
    if not hasattr(llm_policy, "p80_integral"):
        llm_policy.p80_integral = 0.0
    if not hasattr(llm_policy, "last_p80_error"):
        llm_policy.last_p80_error = 0.0
    
    error_level = 50.0 - obs["sump_level"]
    error_p80 = obs["product_p80"] - 75.0
    
    llm_policy.sump_integral += error_level
    llm_policy.sump_integral = max(-120.0, min(120.0, llm_policy.sump_integral))
    
    llm_policy.p80_integral += error_p80
    llm_policy.p80_integral = max(-250.0, min(250.0, llm_policy.p80_integral))
    
    d_p80 = error_p80 - llm_policy.last_p80_error
    llm_policy.last_p80_error = error_p80
    
    kp_pump = 3.2
    ki_pump = 0.28
    pump_speed = 60.0 - (kp_pump * error_level) - (ki_pump * llm_policy.sump_integral)
    pump_speed = max(0.0, min(100.0, pump_speed))
    
    kp_p80 = 16.5
    ki_p80 = 1.15
    kd_p80 = 4.2
    kd_coupling = 1.8
    
    sump_water = 85.0 + (kp_p80 * error_p80) + (ki_p80 * llm_policy.p80_integral) + (kd_p80 * d_p80) - (kd_coupling * error_level)
    sump_water = max(0.0, min(200.0, sump_water))
    
    mill_water = 35.0
    if "circulating_load" in obs and obs["circulating_load"] > 0:
        mill_water = max(15.0, min(85.0, 35.0 + 0.08 * (obs["circulating_load"] - 250.0)))
        
    return float(pump_speed), float(sump_water), float(mill_water)