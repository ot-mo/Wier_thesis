
def llm_policy(obs):
    # Basic proportional control baseline
    error_level = 50.0 - obs["sump_level"]
    
    # Calculate MVs
    pump_speed = 60.0 - (1.5 * error_level)
    sump_water = 50.0 + (1.0 * error_level)
    mill_water = 30.0
    
    return pump_speed, sump_water, mill_water
