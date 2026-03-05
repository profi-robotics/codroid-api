from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict


class OnRobotModel:
    """Supported OnRobot end-effector model identifiers."""

    FG2_7 = "2FG7"
    VGC10 = "VGC10"
    SOFT_GRIPPER = "SOFT_GRIPPER"


class OnRobotAction:
    """Supported OnRobot runtime action names."""

    OPEN = "open"
    CLOSE = "close"
    VACUUM_ON = "vacuum_on"
    VACUUM_OFF = "vacuum_off"
    BLOW_OFF = "blow_off"
    GRIP = "grip"
    RELEASE = "release"


MODEL_CODE_BY_NAME: Dict[str, int] = {
    OnRobotModel.FG2_7: 1,
    OnRobotModel.VGC10: 2,
    OnRobotModel.SOFT_GRIPPER: 3,
}

_ACTIONS_BY_MODEL: Dict[str, set[str]] = {
    OnRobotModel.FG2_7: {OnRobotAction.OPEN, OnRobotAction.CLOSE},
    OnRobotModel.VGC10: {
        OnRobotAction.VACUUM_ON,
        OnRobotAction.VACUUM_OFF,
        OnRobotAction.BLOW_OFF,
    },
    OnRobotModel.SOFT_GRIPPER: {OnRobotAction.GRIP, OnRobotAction.RELEASE},
}

_MODEL_ALIASES: Dict[str, str] = {
    "2FG7": OnRobotModel.FG2_7,
    "FG2_7": OnRobotModel.FG2_7,
    "FG27": OnRobotModel.FG2_7,
    "VGC10": OnRobotModel.VGC10,
    "SOFT": OnRobotModel.SOFT_GRIPPER,
    "SOFT_GRIPPER": OnRobotModel.SOFT_GRIPPER,
    "SOFTGRIPPER": OnRobotModel.SOFT_GRIPPER,
    "SOFT-GRIPPER": OnRobotModel.SOFT_GRIPPER,
}


@dataclass(frozen=True)
class OnRobotProfile:
    """OnRobot tool profile applied through Robot/Control setparam paths.

    Mapping values are provisional defaults and may require controller-specific
    adjustments on real hardware/firmware revisions.
    """

    model: str
    payload_kg: float
    cog_x_m: float
    cog_y_m: float
    cog_z_m: float
    params: Dict[str, Any] = field(default_factory=dict)


def normalize_onrobot_model(model: str) -> str:
    """Normalize user model identifiers into canonical values."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError("OnRobot model must be a non-empty string.")
    key = model.strip().upper().replace(" ", "_")
    normalized = _MODEL_ALIASES.get(key)
    if normalized is None:
        raise ValueError(f"Unsupported OnRobot model: {model!r}")
    return normalized


def onrobot_model_code(model: str) -> int:
    """Resolve controller code for a supported OnRobot model."""
    normalized = normalize_onrobot_model(model)
    return MODEL_CODE_BY_NAME[normalized]


def validate_payload_and_cog(
    payload_kg: float,
    cog_x_m: float,
    cog_y_m: float,
    cog_z_m: float,
) -> None:
    """Validate payload/CoG values before publishing setparam updates."""
    values = {
        "payload_kg": payload_kg,
        "cog_x_m": cog_x_m,
        "cog_y_m": cog_y_m,
        "cog_z_m": cog_z_m,
    }
    for name, value in values.items():
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite number.")
    if float(payload_kg) < 0.0:
        raise ValueError("payload_kg must be >= 0.0")


def validate_model_action(model: str, action: str) -> tuple[str, str]:
    """Validate a runtime action against the selected OnRobot model."""
    normalized_model = normalize_onrobot_model(model)
    if not isinstance(action, str) or not action.strip():
        raise ValueError("OnRobot action must be a non-empty string.")
    normalized_action = action.strip().lower()
    allowed = _ACTIONS_BY_MODEL[normalized_model]
    if normalized_action not in allowed:
        options = ", ".join(sorted(allowed))
        raise ValueError(
            f"Unsupported action {action!r} for model {normalized_model}. "
            f"Allowed: {options}."
        )
    return normalized_model, normalized_action


def default_onrobot_profile(model: str) -> OnRobotProfile:
    """Return a conservative provisional profile for a supported model."""
    normalized = normalize_onrobot_model(model)
    if normalized == OnRobotModel.FG2_7:
        params: Dict[str, Any] = {"force_pct": 50, "speed_pct": 50, "width_mm": 30.0}
        return OnRobotProfile(normalized, 0.0, 0.0, 0.0, 0.0, params=params)
    if normalized == OnRobotModel.VGC10:
        params = {"vacuum_pct": 60, "release_ms": 150}
        return OnRobotProfile(normalized, 0.0, 0.0, 0.0, 0.0, params=params)
    params = {"pressure_pct": 50, "grip_time_ms": 300}
    return OnRobotProfile(normalized, 0.0, 0.0, 0.0, 0.0, params=params)
