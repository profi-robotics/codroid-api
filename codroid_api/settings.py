from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from codroid_api.client import CodroidConfig
from codroid_api.commands import RobotCommandSet, RobotControlPaths


class CodroidSettings(BaseSettings):
    """Codroid connection settings sourced from .env (CODROID_* variables).

    Defaults match the common factory configuration; override in .env as needed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CODROID_",
        extra="ignore",
    )

    host: str = "codroid-controller.local"
    ws_port: int = 9098
    robot_port: int = 9000
    origin: str = "http://codroid-controller.local:9098"
    token: str = "user:YOUR_USERNAME"
    username: str = "YOUR_USERNAME"
    user_password: str = ""
    usercode: str = ""
    userwsid: str = ""
    ws_user_type: str = "wsuser"
    robot_login_name: str = "web"
    robot_password: str = ""
    robot_ws_type: str = "wsrobot"
    websocket_open_timeout_s: float = 5.0
    websocket_close_timeout_s: float = 2.0
    websocket_ping_interval_s: Optional[float] = 20.0
    websocket_ping_timeout_s: Optional[float] = 30.0
    keep_user_web_session: bool = True
    default_language: str = "EN"
    default_project: str = "pjmjbepucimi01gv"
    default_task: str = "tkmjbepuci3lujj8"
    default_label: str = "rumjcr6o3flg6kq0"
    default_stat: int = 2
    default_onlyapi: int = 0
    default_mode: int = 1
    command_power_on: int = 1
    command_power_off: int = 2
    command_manual_mode: int = 3
    command_auto_mode: int = 5
    command_move_home: int = 104
    command_move_safe: int = 1001
    command_move_candle: int = 1002
    command_move_package: int = 105
    command_move_target_linear: int = 106
    command_move_target_optimal: int = 107

    def command_set(self) -> RobotCommandSet:
        return RobotCommandSet(
            power_on=self.command_power_on,
            power_off=self.command_power_off,
            manual_mode=self.command_manual_mode,
            auto_mode=self.command_auto_mode,
            move_home=self.command_move_home,
            move_safe=self.command_move_safe,
            move_candle=self.command_move_candle,
            move_package=self.command_move_package,
            move_target_linear=self.command_move_target_linear,
            move_target_optimal=self.command_move_target_optimal,
        )

    def build_user_config(self) -> CodroidConfig:
        return CodroidConfig(
            host=self.host,
            port=self.ws_port,
            origin=self.origin,
            token=self.token,
            username=self.username,
            user_password=self.user_password,
            usercode=self.usercode,
            userwsid=self.userwsid,
            ws_user_type=self.ws_user_type,
            robot_login_name=self.robot_login_name,
            robot_password=self.robot_password,
            robot_ws_type=self.robot_ws_type,
            websocket_open_timeout_s=self.websocket_open_timeout_s,
            websocket_close_timeout_s=self.websocket_close_timeout_s,
            websocket_ping_interval_s=self.websocket_ping_interval_s,
            websocket_ping_timeout_s=self.websocket_ping_timeout_s,
            default_language=self.default_language,
            default_project=self.default_project,
            default_task=self.default_task,
            default_label=self.default_label,
            default_stat=self.default_stat,
            default_onlyapi=self.default_onlyapi,
            default_mode=self.default_mode,
            commands=self.command_set(),
            control_paths=RobotControlPaths(),
        )

    def build_robot_config(self) -> CodroidConfig:
        return CodroidConfig(
            host=self.host,
            port=self.robot_port,
            origin=self.origin,
            token=self.token,
            username=self.username,
            user_password=self.user_password,
            usercode=self.usercode,
            userwsid=self.userwsid,
            ws_user_type=self.ws_user_type,
            robot_login_name=self.robot_login_name,
            robot_password=self.robot_password,
            robot_ws_type=self.robot_ws_type,
            websocket_open_timeout_s=self.websocket_open_timeout_s,
            websocket_close_timeout_s=self.websocket_close_timeout_s,
            websocket_ping_interval_s=self.websocket_ping_interval_s,
            websocket_ping_timeout_s=self.websocket_ping_timeout_s,
            default_language=self.default_language,
            default_project=self.default_project,
            default_task=self.default_task,
            default_label=self.default_label,
            default_stat=self.default_stat,
            default_onlyapi=self.default_onlyapi,
            default_mode=self.default_mode,
            commands=self.command_set(),
            control_paths=RobotControlPaths(),
        )
