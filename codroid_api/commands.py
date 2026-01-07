from dataclasses import dataclass


@dataclass(frozen=True)
class RobotCommandSet:
    """Command codes sent via Robot/Control/command.

    Add new command codes here and use CodroidAPI.set_robot_command to send them.
    """

    # Power and mode control.
    power_on: int = 1
    power_off: int = 2
    manual_mode: int = 3
    auto_mode: int = 5

    # Preset moves (held command with heartbeat in the UI).
    move_home: int = 104
    move_safe: int = 1001
    move_candle: int = 1002
    move_package: int = 105

    # Targeted moves (use targetPosType + targetAPos/targetCPos first).
    move_target_linear: int = 106
    move_target_optimal: int = 107


@dataclass(frozen=True)
class RobotJogMode:
    """Jog mode values for Robot/Control/jogMode."""

    stop: int = 0
    joint: int = 1
    tcp: int = 2


@dataclass(frozen=True)
class RobotJogReference:
    """Reference frame selector for TCP jogs."""

    coordinate: int = 0
    tool: int = 1


@dataclass(frozen=True)
class RobotTargetPosType:
    """Target position types for point moves (APOS/CPOS families)."""

    none: int = 0
    apos: int = 1
    cpos: int = 2


@dataclass(frozen=True)
class RobotControlPaths:
    """Robot control parameter paths used by setparam calls.

    Add new paths here when wiring new setparam controls.
    """

    command: str = "Robot/Control/command"
    command_heartbeat: str = "Robot/Control/commandHeart"
    manual_move_rate: str = "Robot/Control/manualMoveRate"
    jog_reference: str = "Robot/Control/jogReference"
    jog_mode: str = "Robot/Control/jogMode"
    jog_speed: str = "Robot/Control/jogSpeed"
    jog_index: str = "Robot/Control/jogIndex"
    target_pos_type: str = "Robot/Control/targetPosType"
    target_a_pos: str = "Robot/Control/targetAPos"
    target_c_pos: str = "Robot/Control/targetCPos"
