import time


class PIDController:

    def __init__(
        self,
        Kp: float,
        Ki: float,
        Kd: float,
        setpoint: float = 0.0,
        output_limits: tuple = (None, None),
    ):
        """Initialize the PID controller.

        :param Kp: Proportional gain
        :param Ki: Integral gain
        :param Kd: Derivative gain
        :param setpoint: The target value the controller aims to reach
        :param output_limits: Tuple of (min, max) output limits to prevent
        windup
        """
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.output_limits = output_limits

        # Internal state
        self._integral = 0.0
        self._last_error = 0.0
        self._last_time = None

    def update(
        self, measurement: float, current_time: float = None
    ) -> float:
        """Calculates the PID control output based on the current measurement.

        :param measurement: The current value of the process variable
        :param current_time: Optional explicit timestamp (uses time.time() if
        None)
        :return: Control variable (output)
        """
        if current_time is None:
            current_time = time.time()

        # Handle first run initialization
        if self._last_time is None:
            self._last_time = current_time
            return 0.0

        # Calculate delta time (dt)
        dt = current_time - self._last_time
        if dt <= 0.0:
            return 0.0  # Avoid division by zero if called too quickly

        # 1. Calculate Error
        error = self.setpoint - measurement

        # 2. Proportional Term
        P = self.Kp * error

        # 3. Integral Term (with Anti-Windup limits)
        self._integral += error * dt
        I = self.Ki * self._integral

        # 4. Derivative Term
        derivative = (error - self._last_error) / dt
        D = self.Kd * derivative

        # 5. Compute Total Output
        output = P + I + D

        # 6. Apply Output Limits (Clamping) & Anti-Windup
        min_limit, max_limit = self.output_limits

        if max_limit is not None and output > max_limit:
            output = max_limit
            # Clamp integral to prevent windup
            self._integral -= error * dt
        elif min_limit is not None and output < min_limit:
            output = min_limit
            # Clamp integral to prevent windup
            self._integral -= error * dt

        # Save current state for next iteration
        self._last_error = error
        self._last_time = current_time

        return output

    def reset(self):
        """Resets the internal state of the PID controller."""
        self._integral = 0.0
        self._last_error = 0.0
        self._last_time = None

def run_benchmark(sim, controller, setpoint=100.0, sim_time=150.0, dt=0.1):
    sim = sim (dt=dt)
    temp = sim.reset(initial_temp=25.0)
    steps = int(sim_time / dt)

    if hasattr(controller, "reset"):
        controller.reset()

    iae = 0.0
    total_energy = 0.0
    overshoot = 0.0
    logs = []

    for t in range(steps):
        time_s = t * dt

        # Support both object instance (PIDController) and function call (LLM)
        if isinstance(controller, PIDController):
            u = controller.update(measurement=temp, current_time=time_s)
        else:
            u = controller(setpoint, temp)

        u_clamped = max(0.0, min(100.0, float(u)))
        next_temp = sim.step(u_clamped)

        # Performance accumulation
        error = abs(setpoint - temp)
        iae += error * dt
        total_energy += u_clamped * dt
        if temp > setpoint:
            overshoot = max(overshoot, temp - setpoint)

        if t % 50 == 0:
            logs.append(f"t={time_s:.1f}s | T={temp:.2f}°C | Power={u_clamped:.1f}%")

        temp = next_temp

    return {
        "IAE": round(iae, 2),
        "Total_Energy": round(total_energy, 2),
        "Max_Overshoot": round(overshoot, 2),
        "Final_Temp": round(temp, 2),
        "Logs": "\n".join(logs),
    }

