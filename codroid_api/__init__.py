"""Codroid websocket client API."""

from codroid_api.capture import CodroidCapture, CaptureMessage, extract_send_messages, load_capture
from codroid_api.client import CodroidAPI, CodroidConfig
from codroid_api.commands import (
    RobotCommandSet,
    RobotControlPaths,
    RobotJogMode,
    RobotJogReference,
    RobotTargetPosType,
)
from codroid_api.onrobot import (
    OnRobotAction,
    OnRobotModel,
    OnRobotProfile,
    default_onrobot_profile,
)
from codroid_api.robot_session import RobotPosture, RobotSession
from codroid_api.settings import CodroidSettings

DEFAULT_FLANGE_BUTTON_PORT = 41
DEFAULT_FLANGE_BUTTON_PORTS = (40, 41, 42, 43)

__all__ = [
    "CodroidAPI",
    "CodroidConfig",
    "CodroidSettings",
    "RobotCommandSet",
    "RobotControlPaths",
    "RobotJogMode",
    "RobotJogReference",
    "RobotTargetPosType",
    "OnRobotAction",
    "OnRobotModel",
    "OnRobotProfile",
    "default_onrobot_profile",
    "RobotPosture",
    "RobotSession",
    "CodroidCapture",
    "CaptureMessage",
    "extract_send_messages",
    "load_capture",
    "DEFAULT_FLANGE_BUTTON_PORT",
    "DEFAULT_FLANGE_BUTTON_PORTS",
]
