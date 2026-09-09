def llm_policy(obs):
    if not hasattr(llm_policy, "i_level"):
        llm_policy.i_level = 0.0
        llm_policy.i_p80 = 0.0

    level_err = obs["sump_level"] - 50.0
    llm_policy.i_level = max(-100.0, min(100.0, llm_policy.i_level + level_err))
    
    kp_level = 2.2
    ki_level = 0.08
    pump_speed = 50.0 + (kp_level * level_err) + (ki_level * llm_policy.i_level)
    pump_speed = max(0.0, min(100.0, pump_speed))

    p80_err = obs["product_p80"] - 75.0
    llm_policy.i_p80 = max(-300.0, min(300.0, llm_policy.i_p80 + p80_err))
    
    kp_p80 = 4.0
    ki_p80 = 0.25
    sump_water = 80.0 + (kp_p80 * p80_err) + (ki_p80 * llm_policy.i_p80)
    sump_water = max(0.0, min(200.0, sump_water))

    mill_water = 35.0
    mill_water = max(0.0, min(100.0, mill_water))

    return pump_speed, sump_water, mill_water