def llm_policy(obs):
    if not hasattr(llm_policy, "integral_level"):
        llm_policy.integral_level = 0.0
    
    target_level = 50.0
    target_p80 = 75.0
    
    current_level = obs["sump_level"]
    current_p80 = obs["product_p80"]
    
    error_level = target_level - current_level
    error_p80 = target_p80 - current_p80
    
    llm_policy.integral_level += error_level
    llm_policy.integral_level = max(-150.0, min(150.0, llm_policy.integral_level))
    
    kp_pump = 2.5
    ki_pump = 0.15
    pump_speed = 60.0 - (kp_pump * error_level) - (ki_pump * llm_policy.integral_level)
    pump_speed = max(0.0, min(100.0, pump_speed))
    
    kp_p80 = 6.0
    sump_water = 90.0 - (kp_p80 * error_p80)
    
    sump_water_decoupling = 0.5 * error_level
    sump_water = sump_water + sump_water_decoupling
    sump_water = max(0.0, min(200.0, sump_water))
    
    mill_water = 35.0
    if obs["circulating_load"] > 250.0:
        mill_water = 40.0
    elif obs["circulating_load"] < 150.0:
        mill_water = 30.0
        
    return pump_speed, sump_water, mill_water