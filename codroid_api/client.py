import asyncio
import contextlib
import copy
import json
import logging
import random
import socket
import time
import uuid
import urllib.error
import urllib.request
from urllib.parse import urljoin
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Callable, Dict, Iterable, List, Optional, Tuple

import websockets
from websockets.exceptions import ConnectionClosed

from codroid_api.auto_socket import (
    AutoSocketBackend,
    AutoSocketConfig,
    AutoSocketMode,
    AutoSocketProjectInfo,
    build_auto_socket_project,
)
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
    onrobot_model_code,
    validate_model_action,
    validate_payload_and_cog,
)


LOGGER = logging.getLogger(__name__)


@dataclass
class CodroidConfig:
    """Runtime config for a Codroid websocket connection."""

    # Connection and authentication defaults.
    host: str = "codroid-controller.local"
    port: int = 9098
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

    # Project defaults.
    default_language: str = "EN"
    default_project: str = "pjmjbepucimi01gv"
    default_task: str = "tkmjbepuci3lujj8"
    default_label: str = "rumjcr6o3flg6kq0"
    default_stat: int = 2
    default_onlyapi: int = 0
    default_mode: int = 1

    # Command catalog and control paths.
    commands: RobotCommandSet = field(default_factory=RobotCommandSet)
    control_paths: RobotControlPaths = field(default_factory=RobotControlPaths)
    jog_modes: RobotJogMode = field(default_factory=RobotJogMode)
    jog_references: RobotJogReference = field(
        default_factory=RobotJogReference)
    target_pos_types: RobotTargetPosType = field(
        default_factory=RobotTargetPosType)

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/"


class CodroidAPI:
    EMERGENCY_WARNING_CODE = 269485313
    _EMERGENCY_WARNING_KEYWORDS = (
        "emergency-stop",
        "emergency stop",
        "emergency-stop button",
        "emergency stop button",
    )

    WRONG_TOOL_ERROR_CODE = 269485337
    _WRONG_TOOL_ERROR_KEYWORDS = ("wrong payload", "tool", "payload setting")

    OVERSPEED_WARNING_CODE = 269485334
    _OVERSPEED_WARNING_KEYWORDS = ("overspeed", "speed")

    JOINT_PROTECTION_WARNING_CODE = 269485321
    _JOINT_PROTECTION_WARNING_KEYWORDS = ("joint", "collision")

    DRAG_NOT_ALLOWED_WARNING_CODE = 269485573
    _DRAG_NOT_ALLOWED_WARNING_KEYWORDS = (
        "drag not allowed",
        "external force",
        "payload",
        "tool setting",
    )

    def __init__(self, config: Optional[CodroidConfig] = None) -> None:
        self.config = config or CodroidConfig()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._recv_task: Optional[asyncio.Task[None]] = None
        self._messages: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._is_powered_on: bool = False
        self.active_usercode: str = self.config.usercode or ""
        self.active_userwsid: str = self.config.userwsid or ""
        self.active_user_login_type: str = ""
        self._auto_socket_backend: Optional[AutoSocketBackend] = None

    async def __aenter__(self) -> "CodroidAPI":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._ws is not None:
            return
        connect_kwargs: Dict[str, Any] = {
            "open_timeout": self.config.websocket_open_timeout_s,
            "close_timeout": self.config.websocket_close_timeout_s,
            "ping_interval": self.config.websocket_ping_interval_s,
            "ping_timeout": self.config.websocket_ping_timeout_s,
        }
        if self.config.origin:
            connect_kwargs["origin"] = self.config.origin
        self._ws = await websockets.connect(self.config.ws_url, **connect_kwargs)
        self._recv_task = asyncio.create_task(self._receiver())
        self._recv_task.add_done_callback(self._consume_receiver_exception)

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionClosed, TimeoutError):
                await self._recv_task
            self._recv_task = None
        if self._ws is not None:
            with contextlib.suppress(ConnectionClosed, TimeoutError, OSError):
                await self._ws.close()
            self._ws = None

    @staticmethod
    def _consume_receiver_exception(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        with contextlib.suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                LOGGER.debug("Websocket receiver stopped: %s", exc)

    async def _receiver(self) -> None:
        if self._ws is None:
            return
        try:
            async for message in self._ws:
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    payload = {"raw": message}
                await self._messages.put(payload)
        except asyncio.CancelledError:
            raise
        except ConnectionClosed as exc:
            LOGGER.debug("Websocket receiver closed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Websocket receiver failed: %s", exc, exc_info=True)

    async def listen(self) -> AsyncIterator[Dict[str, Any]]:
        while True:
            message = await self._messages.get()
            yield message

    async def recv(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        if timeout is None:
            return await self._messages.get()
        return await asyncio.wait_for(self._messages.get(), timeout)

    async def send_message(self, message: Dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("Websocket is not connected.")
        await self._ws.send(json.dumps(message))

    async def send_and_wait(
        self,
        message: Dict[str, Any],
        predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
        timeout: Optional[float] = 5.0,
    ) -> Dict[str, Any]:
        await self.send_message(message)
        if predicate is None:
            return await self.recv(timeout=timeout)
        while True:
            incoming = await self.recv(timeout=timeout)
            if predicate(incoming):
                return incoming

    async def replay_capture(
        self,
        messages: Iterable[Dict[str, Any]],
        overrides_by_action: Optional[Dict[str, Dict[str, Any]]] = None,
        delay_seconds: float = 0.0,
        refresh_metadata: bool = True,
        token_override: Optional[str] = None,
    ) -> None:
        for message in messages:
            payload = copy.deepcopy(message)
            if refresh_metadata:
                payload["id"] = self._make_message_id()
                payload["time"] = self._now_ms()
            if token_override is not None:
                payload["token"] = token_override
            if overrides_by_action:
                action = payload.get("action")
                if action in overrides_by_action:
                    self._deep_merge(payload, overrides_by_action[action])
            await self.send_message(payload)
            if delay_seconds:
                await asyncio.sleep(delay_seconds)

    def build_message(
        self,
        message_type: str,
        action: str,
        data: Any,
        token: Optional[str] = None,
        message_id: Optional[str] = None,
        timestamp_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        resolved_token = token or self.config.token
        if not resolved_token:
            raise ValueError("Token is required; set CODROID_TOKEN.")
        return {
            "id": message_id or self._make_message_id(),
            "time": timestamp_ms or self._now_ms(),
            "token": resolved_token,
            "type": message_type,
            "action": action,
            "data": data,
        }

    async def ws_login(
        self,
        username: Optional[str] = None,
        usercode: Optional[str] = None,
        userwsid: Optional[str] = None,
        wstype: Optional[str] = None,
    ) -> None:
        resolved_username = username or self.config.username
        if not resolved_username:
            raise ValueError("Username is required; set CODROID_USERNAME.")
        resolved_usercode = usercode or self.config.usercode
        if not resolved_usercode:
            raise ValueError("Usercode is required; set CODROID_USERCODE.")
        resolved_userwsid = userwsid or self.config.userwsid or self._make_ws_user_id(
            "ws")
        payload = self.build_message(
            message_type="user",
            action="wslogin",
            data={
                "username": resolved_username,
                "usercode": resolved_usercode,
                "userwsid": resolved_userwsid,
                "wstype": wstype or self.config.ws_user_type,
            },
        )
        await self.send_message(payload)
        self.active_usercode = resolved_usercode
        self.active_userwsid = resolved_userwsid
        self.active_user_login_type = "usercode"
        self.config.usercode = resolved_usercode
        self.config.userwsid = resolved_userwsid

    async def ws_login_with_password(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        userwsid: Optional[str] = None,
        wstype: Optional[str] = None,
    ) -> Dict[str, Any]:
        login = await self.http_login(username=username, password=password)
        usercode = login["usercode"]
        resolved_username = username or self.config.username
        resolved_userwsid = userwsid or self._make_ws_user_id("ws")
        await self.ws_login(
            username=resolved_username,
            usercode=usercode,
            userwsid=resolved_userwsid,
            wstype=wstype,
        )
        self.active_usercode = usercode
        self.active_userwsid = resolved_userwsid
        self.active_user_login_type = "password"
        self.config.usercode = usercode
        self.config.userwsid = resolved_userwsid
        return login

    async def http_login(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_username = username or self.config.username
        if not resolved_username:
            raise ValueError("Username is required; set CODROID_USERNAME.")
        resolved_password = (
            password if password is not None else self.config.user_password
        )
        if not resolved_password:
            raise ValueError("Password is required for HTTP login.")
        init_payload = await self._http_json("/code/init", method="GET")
        init_data = init_payload.get("data") or {}
        pub = init_data.get("pub")
        pri = init_data.get("pri")
        if not pub or not pri:
            raise RuntimeError("Failed to fetch login keys from /code/init.")
        login_payload = {
            "code": pub,
            "data": self._asencode(
                json.dumps({"username": resolved_username,
                           "userpass": resolved_password}),
                pri,
            ),
        }
        login_response = await self._http_json("/user/login", method="POST", payload=login_payload)
        decoded = self._decode_login_response(login_response, pri)
        return {
            "usercode": pub,
            "userkey": pri,
            "response": login_response,
            "decoded": decoded,
        }

    async def robot_login(
        self,
        name: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        wstype: Optional[str] = None,
    ) -> None:
        resolved_username = username or self.config.username
        if not resolved_username:
            raise ValueError("Username is required; set CODROID_USERNAME.")
        payload = self.build_message(
            message_type="user",
            action="Login",
            data={
                "name": name or self.config.robot_login_name,
                "password": password if password is not None else self.config.robot_password,
                "username": resolved_username,
                "wstype": wstype or self.config.robot_ws_type,
            },
        )
        await self.send_message(payload)

    def user_logout_payload(self) -> Dict[str, Any]:
        """Build a stable user logout payload using effective login context."""
        return {
            "username": self.config.username,
            "usercode": self.active_usercode or self.config.usercode,
            "userwsid": self.active_userwsid or self.config.userwsid,
            "wstype": self.config.ws_user_type,
        }

    def robot_logout_payload(self) -> Dict[str, Any]:
        """Build a robot logout payload matching the login shape."""
        return {
            "name": self.config.robot_login_name,
            "password": self.config.robot_password,
            "username": self.config.username,
            "wstype": self.config.robot_ws_type,
        }

    def build_user_logout_message(self, action: str = "wslogout") -> Dict[str, Any]:
        """Build a user logout message for the given action."""
        return self.build_message(
            message_type="user",
            action=action,
            data=self.user_logout_payload(),
        )

    def build_robot_logout_message(self, action: str = "Logout") -> Dict[str, Any]:
        """Build a robot logout message for the given action."""
        return self.build_message(
            message_type="user",
            action=action,
            data=self.robot_logout_payload(),
        )

    async def read_config(self) -> None:
        payload = self.build_message(
            message_type="System", action="ReadConfig", data="")
        await self.send_message(payload)

    async def read_system_data(self) -> None:
        payload = self.build_message(
            message_type="projmanager", action="readsystemdata", data="")
        await self.send_message(payload)

    async def read_global_data(self) -> None:
        payload = self.build_message(
            message_type="projmanager", action="readglobaldata", data="")
        await self.send_message(payload)

    async def set_params(self, params: Iterable[Dict[str, Any]]) -> None:
        """Send a batch of setparam control updates."""
        payload = self.build_message(
            message_type="common",
            action="setparam",
            data=list(params),
        )
        await self.send_message(payload)

    async def set_param(self, path: str, value: Any) -> None:
        """Send a single setparam control update."""
        await self.set_params([{"path": path, "value": value}])

    async def set_onrobot_model(self, model: str) -> None:
        """Apply OnRobot model selection using provisional tool mapping."""
        model_code = onrobot_model_code(model)
        await self.set_param(self.config.control_paths.tool_model, model_code)

    async def set_onrobot_payload(
        self,
        payload_kg: float,
        cog_x_m: float,
        cog_y_m: float,
        cog_z_m: float,
    ) -> None:
        """Apply payload and center-of-gravity values for the active OnRobot tool."""
        validate_payload_and_cog(payload_kg, cog_x_m, cog_y_m, cog_z_m)
        await self.set_params(
            [
                {
                    "path": self.config.control_paths.tool_payload,
                    "value": float(payload_kg),
                },
                {
                    "path": self.config.control_paths.tool_cog,
                    "value": {"x": float(cog_x_m), "y": float(cog_y_m), "z": float(cog_z_m)},
                },
            ]
        )

    async def set_onrobot_profile(self, profile: OnRobotProfile) -> None:
        """Apply a full OnRobot profile (model, payload/CoG, model params)."""
        model_code = onrobot_model_code(profile.model)
        validate_payload_and_cog(
            profile.payload_kg,
            profile.cog_x_m,
            profile.cog_y_m,
            profile.cog_z_m,
        )
        await self.set_params(
            [
                {"path": self.config.control_paths.tool_model, "value": model_code},
                {"path": self.config.control_paths.tool_payload, "value": float(profile.payload_kg)},
                {
                    "path": self.config.control_paths.tool_cog,
                    "value": {
                        "x": float(profile.cog_x_m),
                        "y": float(profile.cog_y_m),
                        "z": float(profile.cog_z_m),
                    },
                },
                {
                    "path": self.config.control_paths.tool_params,
                    "value": dict(profile.params),
                },
            ]
        )

    async def onrobot_action(self, model: str, action: str, **kwargs: Any) -> None:
        """Send a model-aware OnRobot runtime action through setparam."""
        normalized_model, normalized_action = validate_model_action(model, action)
        await self.set_param(
            self.config.control_paths.tool_action,
            {
                "model": onrobot_model_code(normalized_model),
                "action": normalized_action,
                "args": dict(kwargs),
            },
        )

    async def onrobot_2fg7_open(
        self,
        width_mm: float = 70.0,
        speed_pct: int = 50,
    ) -> None:
        """Open OnRobot 2FG7 with provisional width/speed arguments."""
        await self.onrobot_action(
            OnRobotModel.FG2_7,
            OnRobotAction.OPEN,
            width_mm=width_mm,
            speed_pct=speed_pct,
        )

    async def onrobot_2fg7_close(
        self,
        width_mm: float = 0.0,
        force_pct: int = 50,
        speed_pct: int = 50,
    ) -> None:
        """Close OnRobot 2FG7 with provisional width/force/speed arguments."""
        await self.onrobot_action(
            OnRobotModel.FG2_7,
            OnRobotAction.CLOSE,
            width_mm=width_mm,
            force_pct=force_pct,
            speed_pct=speed_pct,
        )

    async def onrobot_vgc10_vacuum_on(
        self,
        vacuum_pct: int = 80,
        channel: int = 1,
    ) -> None:
        """Enable VGC10 vacuum output using provisional mapping."""
        await self.onrobot_action(
            OnRobotModel.VGC10,
            OnRobotAction.VACUUM_ON,
            vacuum_pct=vacuum_pct,
            channel=channel,
        )

    async def onrobot_vgc10_vacuum_off(self, channel: int = 1) -> None:
        """Disable VGC10 vacuum output using provisional mapping."""
        await self.onrobot_action(
            OnRobotModel.VGC10,
            OnRobotAction.VACUUM_OFF,
            channel=channel,
        )

    async def onrobot_vgc10_blow_off(
        self,
        duration_ms: int = 250,
        channel: int = 1,
    ) -> None:
        """Trigger VGC10 blow-off output using provisional mapping."""
        await self.onrobot_action(
            OnRobotModel.VGC10,
            OnRobotAction.BLOW_OFF,
            duration_ms=duration_ms,
            channel=channel,
        )

    async def onrobot_soft_gripper_grip(
        self,
        pressure_pct: int = 50,
        duration_ms: int = 300,
    ) -> None:
        """Close Soft Gripper fingers using provisional pressure/duration args."""
        await self.onrobot_action(
            OnRobotModel.SOFT_GRIPPER,
            OnRobotAction.GRIP,
            pressure_pct=pressure_pct,
            duration_ms=duration_ms,
        )

    async def onrobot_soft_gripper_release(self, duration_ms: int = 300) -> None:
        """Release Soft Gripper fingers using provisional duration args."""
        await self.onrobot_action(
            OnRobotModel.SOFT_GRIPPER,
            OnRobotAction.RELEASE,
            duration_ms=duration_ms,
        )

    async def set_robot_command(self, command: int) -> None:
        """Send a robot control command code."""
        if self._auto_socket_backend is not None:
            if command == 0:
                return
            if command in (
                self.config.commands.move_target_linear,
                self.config.commands.move_target_optimal,
            ):
                await self._auto_socket_backend.send_target_move(command)
                return
        await self.set_param(self.config.control_paths.command, command)

    async def send_command_heartbeat(self, timestamp_ms: Optional[int] = None) -> None:
        """Send the command heartbeat used by held moves."""
        if self._auto_socket_backend is not None:
            return
        await self.set_param(
            self.config.control_paths.command_heartbeat,
            timestamp_ms or self._now_ms(),
        )

    async def stop_command(self) -> None:
        """Stop the active command."""
        await self.set_robot_command(0)

    async def power_on(self) -> None:
        """Power on the robot (command code)."""
        await self.set_robot_command(self.config.commands.power_on)
        self._is_powered_on = True

    async def power_off(self) -> None:
        """Power off the robot (command code)."""
        await self.set_robot_command(self.config.commands.power_off)
        self._is_powered_on = False

    async def set_manual_mode(self) -> None:
        """Switch to manual mode (command code)."""
        await self.set_robot_command(self.config.commands.manual_mode)

    async def set_auto_mode(self) -> None:
        """Switch to automatic mode (command code)."""
        await self.set_robot_command(self.config.commands.auto_mode)

    async def detect_emergency_stop(self, message: Dict[str, Any]) -> bool:
        """Detect if message indicates emergency stop condition."""
        # Emergency stop is typically indicated by status/alarm fields in robot messages
        # Look for emergency stop indicators in various message formats
        data = message.get("data", {})

        # Check for emergency stop in robot status
        if isinstance(data, dict):
            # Check robot status fields commonly used for emergency conditions
            robot_status = data.get("robot_status", {})
            if isinstance(robot_status, dict):
                if robot_status.get("emergency_stop") or robot_status.get("estop"):
                    return True

            # Check alarm/error fields
            alarms = data.get("alarms", [])
            if isinstance(alarms, list):
                for alarm in alarms:
                    if isinstance(alarm, dict) and "emergency" in str(alarm).lower():
                        return True

            # Check for emergency indicators in status messages
            if "emergency" in str(data).lower() and "stop" in str(data).lower():
                return True

        if self._message_contains_warning(
            message,
            codes=(self.EMERGENCY_WARNING_CODE,),
            keywords=self._EMERGENCY_WARNING_KEYWORDS,
        ):
            return True

        return False

    async def detect_overspeed(self, message: Dict[str, Any]) -> bool:
        """Detect if message indicates overspeed condition."""
        # Overspeed is typically indicated by status/alarm fields in robot messages
        data = message.get("data", {})

        # Check for overspeed in robot status
        if isinstance(data, dict):
            # Check robot status fields commonly used for overspeed conditions
            robot_status = data.get("robot_status", {})
            if isinstance(robot_status, dict):
                if robot_status.get("overspeed") or robot_status.get("speed_alarm"):
                    return True

            # Check alarm/error fields
            alarms = data.get("alarms", [])
            if isinstance(alarms, list):
                for alarm in alarms:
                    if isinstance(alarm, dict) and "overspeed" in str(alarm).lower():
                        return True

            # Check for overspeed indicators in status messages
            if "overspeed" in str(data).lower() or "speed" in str(data).lower():
                return True

        if self._message_contains_warning(
            message,
            codes=(self.OVERSPEED_WARNING_CODE,),
            keywords=self._OVERSPEED_WARNING_KEYWORDS,
        ):
            return True

        return False

    async def detect_joint_protection(self, message: Dict[str, Any]) -> bool:
        """Detect if message indicates joint protection (collision) condition."""
        if self._message_contains_warning(
            message,
            codes=(self.JOINT_PROTECTION_WARNING_CODE,),
            keywords=self._JOINT_PROTECTION_WARNING_KEYWORDS,
        ):
            return True

        data = message.get("data", {})
        if isinstance(data, dict):
            robot_status = data.get("robot_status", {})
            if isinstance(robot_status, dict):
                # Check for any joint protection flags if available
                if robot_status.get("joint_protection") or robot_status.get("joint_collision"):
                    return True

            alarms = data.get("alarms", [])
            if isinstance(alarms, list):
                for alarm in alarms:
                    if isinstance(alarm, dict) and "joint" in str(alarm).lower():
                        return True

            if "joint" in str(data).lower() or "collision" in str(data).lower():
                return True

        return False

    async def detect_drag_not_allowed(self, message: Dict[str, Any]) -> bool:
        """Detect drag-not-allowed warnings from the controller."""
        if self._message_contains_warning(
            message,
            codes=(self.DRAG_NOT_ALLOWED_WARNING_CODE,),
            keywords=self._DRAG_NOT_ALLOWED_WARNING_KEYWORDS,
        ):
            return True

        data = message.get("data", {})
        if isinstance(data, dict):
            if "drag not allowed" in str(data).lower():
                return True

        return False

    async def detect_wrong_tool_error(self, message: Dict[str, Any]) -> bool:
        """Detect wrong payload/tool errors reported via RobotError."""
        return self.is_wrong_tool_error(message)

    @staticmethod
    def _warning_items(message: Dict[str, Any]) -> List[Any]:
        data = message.get("data")
        if isinstance(data, dict):
            warning_data = data.get("data")
            if isinstance(warning_data, list):
                return warning_data
        return []

    @staticmethod
    def _error_items(message: Dict[str, Any]) -> List[Any]:
        data = message.get("data")
        if isinstance(data, dict):
            error_data = data.get("data")
            if isinstance(error_data, list):
                return error_data
        return []

    @classmethod
    def _warning_matches_entry(
        cls,
        entry: Any,
        *,
        codes: Optional[Iterable[int]] = None,
        keywords: Iterable[str] = (),
    ) -> bool:
        if not isinstance(entry, dict):
            return False
        if codes:
            entry_code = entry.get("errorCode")
            if entry_code in codes:
                return True
        info = entry.get("info") or entry.get("message") or entry.get("msg")
        if isinstance(info, str):
            lowered = info.lower()
            for keyword in keywords:
                if keyword in lowered:
                    return True
        return False

    @classmethod
    def _message_contains_warning(
        cls,
        message: Dict[str, Any],
        *,
        codes: Optional[Iterable[int]] = None,
        keywords: Iterable[str] = (),
        items_getter: Optional[Callable[[Dict[str, Any]], List[Any]]] = None,
    ) -> bool:
        getter = items_getter or cls._warning_items
        for warning in getter(message):
            if cls._warning_matches_entry(warning, codes=codes, keywords=keywords):
                return True
        return False

    @classmethod
    def _matches_emergency_warning_entry(cls, entry: Any) -> bool:
        return cls._warning_matches_entry(
            entry,
            codes=(cls.EMERGENCY_WARNING_CODE,),
            keywords=cls._EMERGENCY_WARNING_KEYWORDS,
        )

    @classmethod
    def is_emergency_button_warning(cls, message: Dict[str, Any]) -> bool:
        if message.get("action") != "RobotWarning":
            return False
        for entry in cls._warning_items(message):
            if cls._matches_emergency_warning_entry(entry):
                return True
        return False

    @classmethod
    def is_overspeed_warning(cls, message: Dict[str, Any]) -> bool:
        if message.get("action") != "RobotWarning":
            return False
        return cls._message_contains_warning(
            message,
            codes=(cls.OVERSPEED_WARNING_CODE,),
            keywords=cls._OVERSPEED_WARNING_KEYWORDS,
        )

    @classmethod
    def is_joint_protection_warning(cls, message: Dict[str, Any]) -> bool:
        if message.get("action") != "RobotWarning":
            return False
        return cls._message_contains_warning(
            message,
            codes=(cls.JOINT_PROTECTION_WARNING_CODE,),
            keywords=cls._JOINT_PROTECTION_WARNING_KEYWORDS,
        )

    @classmethod
    def is_wrong_tool_error(cls, message: Dict[str, Any]) -> bool:
        if message.get("action") != "RobotError":
            return False
        return cls._message_contains_warning(
            message,
            codes=(cls.WRONG_TOOL_ERROR_CODE,),
            keywords=cls._WRONG_TOOL_ERROR_KEYWORDS,
            items_getter=cls._error_items,
        )

    @classmethod
    def is_drag_not_allowed_warning(cls, message: Dict[str, Any]) -> bool:
        if message.get("action") != "RobotWarning":
            return False
        return cls._message_contains_warning(
            message,
            codes=(cls.DRAG_NOT_ALLOWED_WARNING_CODE,),
            keywords=cls._DRAG_NOT_ALLOWED_WARNING_KEYWORDS,
        )

    @classmethod
    def is_robot_warning_cleared(cls, message: Dict[str, Any]) -> bool:
        if message.get("action") != "RobotWarning":
            return False
        warning_items = cls._warning_items(message)
        return bool(isinstance(warning_items, list) and not warning_items)

    async def watch_emergency_button(self) -> AsyncIterator[Dict[str, Any]]:
        """Yield emergency button events based on RobotWarning updates."""
        emergency_active = False
        async for message in self.listen():
            if message.get("action") != "RobotWarning":
                continue

            timestamp = message.get("time") or self._now_ms()

            if self.is_emergency_button_warning(message):
                if not emergency_active:
                    emergency_active = True
                    yield {
                        "event": "pressed",
                        "timestamp": timestamp,
                        "message": message,
                    }
                continue

            if emergency_active and self.is_robot_warning_cleared(message):
                emergency_active = False
                yield {
                    "event": "released",
                    "timestamp": timestamp,
                    "message": message,
                }

    async def watch_drag_not_allowed(self) -> AsyncIterator[Dict[str, Any]]:
        """Yield drag-not-allowed events based on RobotWarning updates."""
        drag_active = False
        async for message in self.listen():
            if message.get("action") != "RobotWarning":
                continue

            timestamp = message.get("time") or self._now_ms()

            if self.is_drag_not_allowed_warning(message):
                if not drag_active:
                    drag_active = True
                    yield {
                        "event": "detected",
                        "timestamp": timestamp,
                        "message": message,
                    }
                continue

            if drag_active and self.is_robot_warning_cleared(message):
                drag_active = False
                yield {
                    "event": "cleared",
                    "timestamp": timestamp,
                    "message": message,
                }

    async def watch_rescue_mode(self) -> AsyncIterator[Dict[str, Any]]:
        """Track rescue mode state transitions emitted through RobotStatus."""
        rescue_active = False
        async for message in self.listen():
            if message.get("action") != "RobotStatus":
                continue

            status_data = message.get("data", {}).get("data", {}) or {}
            state = status_data.get("state")
            if state is None:
                continue

            timestamp = message.get("time") or self._now_ms()

            if state == 3 and not rescue_active:
                rescue_active = True
                yield {
                    "event": "entered",
                    "timestamp": timestamp,
                    "state": state,
                    "message": message,
                }
            elif state != 3 and rescue_active:
                rescue_active = False
                yield {
                    "event": "exited",
                    "timestamp": timestamp,
                    "state": state,
                    "message": message,
                }

    async def clear_robot_error(self) -> None:
        """Send the UI error-clear command (501) used after emergency conditions."""
        await self.set_robot_command(self.config.commands.clear_error)

    async def _run_error_recovery_sequence(self) -> None:
        """Shared recovery sequence for clearing alarmed states."""
        await self.stop_command()
        await asyncio.sleep(0.1)
        await self.power_off()
        await asyncio.sleep(0.5)
        await self.power_on()
        await asyncio.sleep(0.5)
        await self.clear_robot_error()
        await asyncio.sleep(0.2)

    async def clear_emergency_stop(self) -> None:
        """Clear emergency stop condition by following the standard recovery sequence."""
        await self._run_error_recovery_sequence()

    async def clear_overspeed(self) -> None:
        """Clear overspeed condition by following the standard recovery sequence."""
        await self._run_error_recovery_sequence()

    async def clear_joint_protection(self) -> None:
        """Clear joint protection error (e.g., collision) using the recovery sequence."""
        await self._run_error_recovery_sequence()

    async def clear_tool_error(self) -> None:
        """Clear wrong tool/payload errors using the shared recovery sequence."""
        await self._run_error_recovery_sequence()

    async def clear_drag_not_allowed(self) -> None:
        """Clear the drag-not-allowed warning by using the UI clear command."""
        await self.clear_robot_error()

    async def enter_rescue_mode(self) -> None:
        """Trigger the Rescue mode command (Robot/Control/command = 4)."""
        await self._ensure_powered_off()
        await self.set_robot_command(self.config.commands.rescue_mode)

    async def exit_rescue_mode(self) -> None:
        """Exit Rescue mode by powering off the robot (command 2)."""
        await self.power_off()

    async def _ensure_powered_off(self) -> None:
        """Ensure the robot is powered off before sending Rescue commands."""
        if self._is_powered_on:
            await self.power_off()
            await asyncio.sleep(0.5)

    async def monitor_robot_errors(self) -> AsyncIterator[Dict[str, Any]]:
        """Monitor incoming messages for emergency stop, overspeed, joint protection, or tool errors.

        Yields:
            Dict with keys: error_type (str), detected (bool), message (dict), timestamp (int)
        """
        async for message in self.listen():
            timestamp = self._now_ms()

            # Check for emergency stop
            if await self.detect_emergency_stop(message):
                yield {
                    "error_type": "emergency_stop",
                    "detected": True,
                    "message": message,
                    "timestamp": timestamp,
                }

            # Check for overspeed
            if await self.detect_overspeed(message):
                yield {
                    "error_type": "overspeed",
                    "detected": True,
                    "message": message,
                    "timestamp": timestamp,
                }

            # Check for joint protection / collision events
            if await self.detect_joint_protection(message):
                yield {
                    "error_type": "joint_protection",
                    "detected": True,
                    "message": message,
                    "timestamp": timestamp,
                }

            # Check for drag-not-allowed warnings
            if await self.detect_drag_not_allowed(message):
                yield {
                    "error_type": "drag_not_allowed",
                    "detected": True,
                    "message": message,
                    "timestamp": timestamp,
                }

            # Check for wrong tool/payload errors
            if await self.detect_wrong_tool_error(message):
                yield {
                    "error_type": "wrong_tool",
                    "detected": True,
                    "message": message,
                    "timestamp": timestamp,
                }

    async def auto_recover_from_errors(
        self,
        monitor_duration: Optional[float] = None,
        auto_clear: bool = True,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Monitor for errors and optionally auto-clear them.

        Args:
            monitor_duration: How long to monitor in seconds. If None, monitors indefinitely.
            auto_clear: Whether to automatically clear detected errors.

        Yields:
            Dict with error info and recovery status.
        """
        start_time = time.monotonic()

        async for error_event in self.monitor_robot_errors():
            # Check if monitoring duration has elapsed
            if monitor_duration is not None:
                elapsed = time.monotonic() - start_time
                if elapsed > monitor_duration:
                    break

            error_type = error_event["error_type"]

            if auto_clear:
                try:
                    if error_type == "emergency_stop":
                        await self.clear_emergency_stop()
                        recovery_status = "cleared"
                    elif error_type == "overspeed":
                        await self.clear_overspeed()
                        recovery_status = "cleared"
                    elif error_type == "joint_protection":
                        await self.clear_joint_protection()
                        recovery_status = "cleared"
                    elif error_type == "drag_not_allowed":
                        await self.clear_drag_not_allowed()
                        recovery_status = "cleared"
                    elif error_type == "wrong_tool":
                        await self.clear_tool_error()
                        recovery_status = "cleared"
                    else:
                        recovery_status = "unknown_error_type"
                except Exception as e:
                    recovery_status = f"clear_failed: {e}"
            else:
                recovery_status = "detected_only"

            yield {
                **error_event,
                "recovery_status": recovery_status,
                "auto_clear_enabled": auto_clear,
            }

    async def set_manual_move_rate(self, rate: float) -> None:
        """Set the manual movement rate (0.0 - 1.0) via setparam."""
        await self.set_param(self.config.control_paths.manual_move_rate, rate)

    async def set_speed_multiplier(self, rate: float) -> None:
        """Alias for setting the manual movement rate."""
        await self.set_manual_move_rate(rate)

    async def set_jog_reference(self, reference: int) -> None:
        """Set jog reference (coordinate/tool) via setparam."""
        await self.set_param(self.config.control_paths.jog_reference, reference)

    async def set_jog_mode(self, mode: int) -> None:
        """Set jog mode (stop/joint/tcp) via setparam."""
        await self.set_param(self.config.control_paths.jog_mode, mode)

    async def set_jog_speed(self, speed: int) -> None:
        """Set jog direction/speed (-1/0/1) via setparam."""
        await self.set_param(self.config.control_paths.jog_speed, speed)

    async def set_jog_index(self, index: int) -> None:
        """Set jog axis index (1-6) via setparam."""
        await self.set_param(self.config.control_paths.jog_index, index)

    async def start_jog(
        self,
        mode: int,
        axis: int,
        direction: int,
        send_heartbeat: bool = True,
    ) -> None:
        """Start a jog movement and optionally send a heartbeat for held moves."""
        await self.set_jog_mode(mode)
        await self.set_jog_speed(direction)
        await self.set_jog_index(axis)
        if send_heartbeat:
            await self.send_command_heartbeat()

    async def start_joint_jog(
        self, axis: int, direction: int, send_heartbeat: bool = True
    ) -> None:
        """Jog a single joint axis (1-6)."""
        await self.start_jog(
            self.config.jog_modes.joint,
            axis=axis,
            direction=direction,
            send_heartbeat=send_heartbeat,
        )

    async def start_tcp_jog(
        self,
        axis: int,
        direction: int,
        reference: Optional[int] = None,
        send_heartbeat: bool = True,
    ) -> None:
        """Jog TCP coordinates in a selected reference frame."""
        resolved_reference = (
            reference
            if reference is not None
            else self.config.jog_references.coordinate
        )
        await self.set_jog_reference(resolved_reference)
        await self.start_jog(
            self.config.jog_modes.tcp,
            axis=axis,
            direction=direction,
            send_heartbeat=send_heartbeat,
        )

    async def start_tcp_jog_coordinate(
        self, axis: int, direction: int, send_heartbeat: bool = True
    ) -> None:
        """Jog TCP coordinates in the coordinate reference frame."""
        await self.start_tcp_jog(
            axis=axis,
            direction=direction,
            reference=self.config.jog_references.coordinate,
            send_heartbeat=send_heartbeat,
        )

    async def start_tcp_jog_tool(
        self, axis: int, direction: int, send_heartbeat: bool = True
    ) -> None:
        """Jog TCP coordinates in the tool reference frame."""
        await self.start_tcp_jog(
            axis=axis,
            direction=direction,
            reference=self.config.jog_references.tool,
            send_heartbeat=send_heartbeat,
        )

    async def hold_jog(
        self,
        mode: int,
        axis: int,
        direction: int,
        hold_seconds: float,
        heartbeat_interval: float = 0.5,
    ) -> None:
        """Hold a jog command, sending heartbeats until the hold ends."""
        await self.start_jog(
            mode=mode,
            axis=axis,
            direction=direction,
            send_heartbeat=True,
        )
        if hold_seconds <= 0:
            return
        deadline = time.monotonic() + hold_seconds
        while time.monotonic() < deadline:
            await self.send_command_heartbeat()
            await asyncio.sleep(heartbeat_interval)
        await self.stop_jog()

    async def stop_jog(self) -> None:
        """Stop the active jog movement."""
        await self.set_jog_mode(self.config.jog_modes.stop)
        await self.set_jog_index(0)
        await self.set_jog_speed(0)

    async def set_target_pos_type(self, pos_type: int) -> None:
        """Select a target position type (APOS/CPOS families)."""
        if self._auto_socket_backend is not None:
            self._auto_socket_backend.set_target_pos_type(pos_type)
            return
        await self.set_param(self.config.control_paths.target_pos_type, pos_type)

    async def set_target_apos(self, position: Dict[str, Any]) -> None:
        """Set a joint-space target position payload."""
        if self._auto_socket_backend is not None:
            self._auto_socket_backend.set_target_apos(position)
            return
        await self.set_param(self.config.control_paths.target_a_pos, position)

    async def set_target_cpos(self, position: Dict[str, Any]) -> None:
        """Set a cartesian target position payload."""
        if self._auto_socket_backend is not None:
            self._auto_socket_backend.set_target_cpos(position)
            return
        await self.set_param(self.config.control_paths.target_c_pos, position)

    async def clear_target_position(self) -> None:
        """Clear the active target position selection."""
        if self._auto_socket_backend is not None:
            self._auto_socket_backend.clear_target_position()
            return
        await self.set_target_pos_type(self.config.target_pos_types.none)

    async def move_to_target_linear(self, reset_after: bool = True) -> None:
        """Execute a straight-line move to the current target position."""
        await self._execute_target_move(
            self.config.commands.move_target_linear, reset_after=reset_after
        )

    async def move_to_target_optimal(self, reset_after: bool = True) -> None:
        """Execute an optimal-path move to the current target position."""
        await self._execute_target_move(
            self.config.commands.move_target_optimal, reset_after=reset_after
        )

    async def move_to_joint_target_linear(
        self, position: Dict[str, Any], reset_after: bool = True
    ) -> None:
        """Move to a joint target (APOS/DAPOS) in a straight line."""
        await self.set_target_pos_type(self.config.target_pos_types.apos)
        await self.set_target_apos(position)
        await self.move_to_target_linear(reset_after=reset_after)

    async def move_to_joint_target_optimal(
        self, position: Dict[str, Any], reset_after: bool = True
    ) -> None:
        """Move to a joint target (APOS/DAPOS) with optimal path planning."""
        await self.set_target_pos_type(self.config.target_pos_types.apos)
        await self.set_target_apos(position)
        await self.move_to_target_optimal(reset_after=reset_after)

    async def move_to_cartesian_target_linear(
        self, position: Dict[str, Any], reset_after: bool = True
    ) -> None:
        """Move to a cartesian target (CPOS/DCPOS) in a straight line."""
        await self.set_target_pos_type(self.config.target_pos_types.cpos)
        await self.set_target_cpos(position)
        await self.move_to_target_linear(reset_after=reset_after)

    async def move_to_cartesian_target_optimal(
        self, position: Dict[str, Any], reset_after: bool = True
    ) -> None:
        """Move to a cartesian target (CPOS/DCPOS) with optimal path planning."""
        await self.set_target_pos_type(self.config.target_pos_types.cpos)
        await self.set_target_cpos(position)
        await self.move_to_target_optimal(reset_after=reset_after)

    async def move_to_coordinate_origin(
        self,
        coordinate_id: int,
        linear: bool = True,
        reset_after: bool = True,
        hold_seconds: float = 5.0,
        heartbeat_interval: float = 0.5,
    ) -> None:
        """Move the robot to the origin of a user coordinate frame."""
        origin = self.build_target_cpos(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        origin = self.attach_coordinate_to_cpos(
            origin, coordinate_id=coordinate_id)

        # Select the coordinate slot so downstream publishes reflect the correct frame.
        await self.set_current_coordinate_id(coordinate_id)

        await self.set_target_pos_type(self.config.target_pos_types.cpos)
        await self.set_target_cpos(origin)
        command = (
            self.config.commands.move_target_linear
            if linear
            else self.config.commands.move_target_optimal
        )
        await self.set_robot_command(command)

        if hold_seconds > 0:
            deadline = time.monotonic() + hold_seconds
            while time.monotonic() < deadline:
                await self.send_command_heartbeat()
                await asyncio.sleep(heartbeat_interval)

        if reset_after:
            await self.clear_target_position()
            await self.stop_command()

    async def update_posture(
        self,
        x: float,
        y: float,
        z: float,
        a: float,
        b: float,
        c: float,
        e: float = 0.0,
        cf: Optional[Iterable[int]] = None,
        reset_after: bool = True,
    ) -> None:
        """Update posture using a CPOS target with poscfg mode 4."""
        poscfg = self.build_poscfg(mode=4, cf=cf)
        position = self.build_target_cpos(x, y, z, a, b, c, e=e, poscfg=poscfg)
        await self.move_to_cartesian_target_linear(position, reset_after=reset_after)

    @staticmethod
    def build_poscfg(mode: int = 0, cf: Optional[Iterable[int]] = None) -> Dict[str, int]:
        """Build a poscfg payload for cartesian targets."""
        cf_values = list(cf) if cf is not None else [-1] * 7
        if len(cf_values) < 7:
            cf_values.extend([-1] * (7 - len(cf_values)))
        cf_values = cf_values[:7]
        return {
            "mode": mode,
            "cf1": cf_values[0],
            "cf2": cf_values[1],
            "cf3": cf_values[2],
            "cf4": cf_values[3],
            "cf5": cf_values[4],
            "cf6": cf_values[5],
            "cf7": cf_values[6],
        }

    @staticmethod
    def build_target_apos(
        joints: Iterable[float], exjoints: Optional[Iterable[float]] = None
    ) -> Dict[str, float]:
        """Build the APOS/DAPOS payload from joint values."""
        joint_values = list(joints)
        if len(joint_values) < 6:
            raise ValueError("APOS requires at least 6 joint values.")
        if len(joint_values) < 7:
            joint_values.append(0.0)
        joint_values = joint_values[:7]
        ex_values = list(exjoints) if exjoints is not None else []
        if len(ex_values) < 10:
            ex_values.extend([0.0] * (10 - len(ex_values)))
        ex_values = ex_values[:10]
        payload = {
            "jntpos1": joint_values[0],
            "jntpos2": joint_values[1],
            "jntpos3": joint_values[2],
            "jntpos4": joint_values[3],
            "jntpos5": joint_values[4],
            "jntpos6": joint_values[5],
            "jntpos7": joint_values[6],
        }
        for idx, value in enumerate(ex_values, start=1):
            payload[f"exjntpos{idx}"] = value
        return payload

    @staticmethod
    def build_target_cpos(
        x: float,
        y: float,
        z: float,
        a: float,
        b: float,
        c: float,
        e: float = 0.0,
        poscfg: Optional[Dict[str, int]] = None,
        exjoints: Optional[Iterable[float]] = None,
    ) -> Dict[str, Any]:
        """Build the CPOS/DCPOS payload from cartesian values."""
        payload: Dict[str, Any] = {
            "x": x,
            "y": y,
            "z": z,
            "a": a,
            "b": b,
            "c": c,
            "e": e,
            "poscfg": poscfg or CodroidAPI.build_poscfg(),
        }
        ex_values = list(exjoints) if exjoints is not None else []
        if len(ex_values) < 10:
            ex_values.extend([0.0] * (10 - len(ex_values)))
        ex_values = ex_values[:10]
        for idx, value in enumerate(ex_values, start=1):
            payload[f"exjntpos{idx}"] = value
        return payload

    async def set_current_coordinate_id(
        self,
        coordinate_id: int,
        *,
        await_ack: bool = False,
        timeout: float = 5.0,
    ) -> Optional[Dict[str, Any]]:
        """Select the active coordinate system.

        When await_ack is True, waits for the SetCurrentCoordinateId response.
        Avoid await_ack when another task is already consuming listen()/recv().
        """
        payload = self.build_message(
            message_type="Robot",
            action="SetCurrentCoordinateId",
            data=coordinate_id,
        )
        if not await_ack:
            await self.send_message(payload)
            return None

        def _is_coordinate_response(msg: Dict[str, Any]) -> bool:
            return (
                msg.get("type") == "Robot"
                and msg.get("action") == "SetCurrentCoordinateId"
                and msg.get("id") == payload["id"]
            )

        response = await self.send_and_wait(
            payload,
            predicate=_is_coordinate_response,
            timeout=timeout,
        )
        return (response.get("data") or {}).get("data", {})

    @staticmethod
    def attach_coordinate_to_cpos(
        cpos: Dict[str, Any],
        coordinate_id: int,
    ) -> Dict[str, Any]:
        """Return a CPOS payload tagged with the desired user coordinate ID."""
        payload = copy.deepcopy(cpos)
        payload.setdefault("e", 0.0)
        payload.setdefault("poscfg", CodroidAPI.build_poscfg())
        for idx in range(1, 11):
            payload.setdefault(f"exjntpos{idx}", 0.0)
        payload["coord"] = {
            "datavar": {
                "default": "DEFAULT",
                "type": "USERCOOR",
                "value": coordinate_id,
            }
        }
        return payload

    async def coordinate_calibration(
        self,
        points: Iterable[Dict[str, Any]],
        coordinate_id: int,
        timeout: float = 5.0,
        set_active_coordinate: bool = True,
    ) -> Dict[str, Any]:
        """Run a three-point coordinate calibration and return the computed frame.

        Args:
            points: Iterable of three CPOS-like dictionaries (x, y, z, a, b, c, etc.).
            coordinate_id: User coordinate slot to tag in the calibration payload.
            timeout: Seconds to wait for the CoordinateCalibration response.
            set_active_coordinate: When True, send SetCurrentCoordinateId before calibrating.
        """
        payload_points: List[Dict[str, Any]] = []
        for point in points:
            payload_points.append(
                self.attach_coordinate_to_cpos(
                    point, coordinate_id=coordinate_id)
            )

        if len(payload_points) != 3:
            raise ValueError(
                "Coordinate calibration requires exactly three points.")

        if set_active_coordinate:
            await self.set_current_coordinate_id(coordinate_id)

        message = self.build_message(
            message_type="Robot",
            action="CoordinateCalibration",
            data=payload_points,
        )

        def _is_calibration_response(msg: Dict[str, Any]) -> bool:
            return (
                msg.get("type") == "Robot"
                and msg.get("action") == "CoordinateCalibration"
            )

        response = await self.send_and_wait(
            message,
            predicate=_is_calibration_response,
            timeout=timeout,
        )
        return (response.get("data") or {}).get("data", {})

    async def change_coordinate_parameter(
        self,
        coordinate_id: int,
        frame: Dict[str, Any],
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Persist a calibrated coordinate frame into a user coordinate slot."""
        payload = {
            "id": coordinate_id,
            "x": frame["x"],
            "y": frame["y"],
            "z": frame["z"],
            "a": frame["a"],
            "b": frame["b"],
            "c": frame["c"],
        }
        message = self.build_message(
            message_type="Robot",
            action="ChangeCoordinateParameter",
            data=payload,
        )

        def _is_change_response(msg: Dict[str, Any]) -> bool:
            return (
                msg.get("type") == "Robot"
                and msg.get("action") == "ChangeCoordinateParameter"
            )

        response = await self.send_and_wait(
            message,
            predicate=_is_change_response,
            timeout=timeout,
        )
        return (response.get("data") or {}).get("data", {})

    async def move_home(self, hold_seconds: float = 0.0) -> None:
        """Move to the Home preset; optionally hold with heartbeat."""
        await self._move_to_preset(self.config.commands.move_home, hold_seconds)

    async def move_safe(self, hold_seconds: float = 0.0) -> None:
        """Move to the Safe preset; optionally hold with heartbeat."""
        await self._move_to_preset(self.config.commands.move_safe, hold_seconds)

    async def move_candle(self, hold_seconds: float = 0.0) -> None:
        """Move to the Candle preset; optionally hold with heartbeat."""
        await self._move_to_preset(self.config.commands.move_candle, hold_seconds)

    async def move_package(self, hold_seconds: float = 0.0) -> None:
        """Move to the Package preset; optionally hold with heartbeat."""
        await self._move_to_preset(self.config.commands.move_package, hold_seconds)

    async def set_variable_state(self, state: int) -> None:
        """Set VariableState (used during project execution)."""
        payload = self.build_message(
            message_type="projexecute",
            action="VariableState",
            data=state,
        )
        await self.send_message(payload)

    async def get_record_flag(self) -> None:
        """Query the current trajectory record flag."""
        payload = self.build_message(
            message_type="trajectory", action="getRecordFlag", data="")
        await self.send_message(payload)

    async def set_record_flag(self, enabled: bool) -> None:
        """Enable/disable trajectory recording."""
        payload = self.build_message(
            message_type="trajectory",
            action="setRecordFlag",
            data=enabled,
        )
        await self.send_message(payload)

    async def get_trajectory_list(self) -> None:
        """Request the available trajectory list."""
        payload = self.build_message(
            message_type="trajectory", action="getTrajectoryList", data=""
        )
        await self.send_message(payload)

    async def get_trajectory_dir(self) -> None:
        """Request the trajectory directory info."""
        payload = self.build_message(
            message_type="trajectory", action="getTrajectoryDir", data=""
        )
        await self.send_message(payload)

    async def get_log_file_list(self) -> None:
        """Request log file metadata."""
        payload = self.build_message(
            message_type="common", action="getLogFileList", data={})
        await self.send_message(payload)

    @staticmethod
    def parse_io_info(message: Dict[str, Any]) -> Dict[str, Dict[int, str]]:
        """Extract IO names from an IOManager/GetIOInfo response."""
        data = (message.get("data") or {}).get("data") or {}
        result: Dict[str, Dict[int, str]] = {}
        for key in ("DI", "DO", "AI", "AO"):
            entries = data.get(key) or []
            ports: Dict[int, str] = {}
            for entry in entries:
                try:
                    port = int(entry.get("port"))
                except Exception:
                    continue
                name = str(entry.get("name") or port)
                ports[port] = name
            if ports:
                result[key] = ports
        return result

    @staticmethod
    def extract_di_state(message: Dict[str, Any]) -> Dict[int, int]:
        """Return DI state map (port -> value) from any websocket payload if present."""

        def _parse_di_payload(payload: Any) -> Dict[int, int]:
            states: Dict[int, int] = {}
            if isinstance(payload, list):
                # List of dicts or list of ints.
                if payload and isinstance(payload[0], dict):
                    for item in payload:
                        try:
                            port = int(item.get("port"))
                        except Exception:
                            continue
                        value = item.get("value")
                        if value is None:
                            value = item.get("forced")
                        try:
                            states[port] = int(value)
                        except Exception:
                            states[port] = 0
                else:
                    for idx, value in enumerate(payload):
                        try:
                            states[idx] = int(value)
                        except Exception:
                            states[idx] = 0
            elif isinstance(payload, dict):
                for key, value in payload.items():
                    try:
                        port = int(key)
                    except Exception:
                        continue
                    try:
                        states[port] = int(value)
                    except Exception:
                        states[port] = 0
            return states

        di_states: Dict[int, int] = {}

        # Fast path: IOManager/GetIOValue response.
        if (
            message.get("type") == "IOManager"
            and message.get("action") == "GetIOValue"
        ):
            payload = (message.get("data") or {}).get("data") or []
            di_states.update(_parse_di_payload(payload))
            if di_states:
                return di_states

        def _walk(obj: Any) -> None:
            nonlocal di_states
            if isinstance(obj, dict):
                for key, value in obj.items():
                    lowered = key.lower()
                    if lowered in {"di", "distate", "di_state"}:
                        di_states.update(_parse_di_payload(value))
                    else:
                        _walk(value)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)

        _walk(message)
        return di_states

    async def get_io_info(self) -> None:
        payload = self.build_message(
            message_type="IOManager", action="GetIOInfo", data="")
        await self.send_message(payload)

    async def watch_di_changes(
        self,
        ports: Optional[Iterable[int]] = None,
        interval: float = 0.2,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield DI edge events by polling IOManager/GetIOValue.

        Args:
            ports: Iterable of DI ports to poll. If None, will use the DI list
                returned from the first GetIOInfo response; otherwise falls back
                to the pendant/flange DI defaults observed in the UI (0–15, 32,
                33, 40–45).
            interval: Polling interval in seconds.

        Yields:
            Dict with keys: port (int), value (int), label (str).
        """
        default_ports: Tuple[int, ...] = (
            0,
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            32,
            33,
            40,
            41,
            42,
            43,
            44,
            45,
        )
        di_ports: List[int] = list(ports) if ports is not None else []
        button_labels: Dict[int, str] = {}
        di_state: Dict[int, int] = {}
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

        async def _listener() -> None:
            nonlocal di_ports, button_labels, di_state
            async for msg in self.listen():
                if msg.get("type") == "IOManager" and msg.get("action") == "GetIOInfo":
                    io_names = self.parse_io_info(msg)
                    di_names = io_names.get("DI") or {}
                    if di_names:
                        button_labels.update(di_names)
                        if not di_ports:
                            di_ports = sorted(di_names.keys())

                current = self.extract_di_state(msg)
                if not current:
                    continue
                for port, value in current.items():
                    prev = di_state.get(port)
                    if prev is not None and prev != value:
                        queue.put_nowait(
                            {
                                "port": port,
                                "value": value,
                                "label": button_labels.get(port, f"DI{port}"),
                            }
                        )
                    di_state[port] = value

        listener_task = asyncio.create_task(_listener())
        try:
            # Kick off GetIOInfo to populate labels/ports if needed.
            if not di_ports:
                await self.get_io_info()

            while True:
                poll_ports = di_ports or list(default_ports)
                request = self.build_message(
                    message_type="IOManager",
                    action="GetIOValue",
                    data=poll_ports,
                )
                await self.send_message(request)

                # Drain any queued edges before sleeping.
                while not queue.empty():
                    try:
                        yield queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                await asyncio.sleep(interval)
        finally:
            listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener_task

    async def set_language(self, language: Optional[str] = None) -> None:
        payload = self.build_message(
            message_type="common",
            action="setlanguage",
            data=language or self.config.default_language,
        )
        await self.send_message(payload)

    async def run_project(
        self,
        proj: Optional[str] = None,
        task: Optional[str] = None,
        label: Optional[str] = None,
        stat: Optional[int] = None,
        onlyapi: Optional[int] = None,
        mode: Optional[int] = None,
    ) -> None:
        resolved_proj = proj or self.config.default_project
        resolved_task = task or self.config.default_task
        resolved_label = label or self.config.default_label
        if not resolved_proj:
            raise ValueError(
                "Project id is required; set CODROID_DEFAULT_PROJECT.")
        if not resolved_task:
            raise ValueError("Task id is required; set CODROID_DEFAULT_TASK.")
        if not resolved_label:
            raise ValueError(
                "Label id is required; set CODROID_DEFAULT_LABEL.")
        payload = self.build_message(
            message_type="projexecute",
            action="run",
            data={
                "proj": resolved_proj,
                "task": resolved_task,
                "label": resolved_label,
                "stat": self.config.default_stat if stat is None else stat,
                "onlyapi": self.config.default_onlyapi if onlyapi is None else onlyapi,
                "mode": self.config.default_mode if mode is None else mode,
            },
        )
        await self.send_message(payload)

    async def stop_project(self) -> None:
        payload = self.build_message(
            message_type="projexecute", action="stop", data={})
        await self.send_message(payload)

    def build_auto_socket_project(
        self,
        config: Optional[AutoSocketConfig] = None,
    ) -> AutoSocketProjectInfo:
        """Build, but do not save, an auto-mode socket project document."""
        resolved_config = self._resolve_auto_socket_config(
            config or AutoSocketConfig()
        )
        return build_auto_socket_project(
            resolved_config,
            controller_host=self.config.host,
            id_factory=self._node_id,
        )

    async def install_auto_socket_project(
        self,
        config: Optional[AutoSocketConfig] = None,
    ) -> AutoSocketProjectInfo:
        """Generate and save an auto-mode socket project on the controller."""
        info = self.build_auto_socket_project(config)
        if info.project is None:
            raise RuntimeError("Generated auto socket project is missing document data.")
        await self.save_project(info.project)
        return info

    async def start_auto_socket_project(
        self,
        project_info: AutoSocketProjectInfo,
        config: Optional[AutoSocketConfig] = None,
    ) -> None:
        """Run an auto socket project in controller auto mode."""
        resolved_config = self._resolve_auto_socket_config(
            config or AutoSocketConfig()
        )
        await self.run_project(
            proj=project_info.project_id,
            task=project_info.task_id,
            label=project_info.run_label_id,
            stat=resolved_config.run_stat,
            onlyapi=resolved_config.run_onlyapi,
            mode=resolved_config.run_mode,
        )

    async def stop_auto_socket_project(self) -> None:
        """Stop the active controller project used by auto socket mode."""
        await self.stop_project()

    def auto_socket_mode(
        self,
        config: Optional[AutoSocketConfig] = None,
        *,
        project_info: Optional[AutoSocketProjectInfo] = None,
    ) -> AutoSocketMode:
        """Return a context manager that routes target moves over TCP."""
        return AutoSocketMode(
            self,
            config or AutoSocketConfig(),
            project_info=project_info,
        )

    def _resolve_auto_socket_config(
        self,
        config: AutoSocketConfig,
    ) -> AutoSocketConfig:
        if config.socket_role.lower() != "server":
            return config
        if config.project_socket_host:
            return config
        advertised_host = config.socket_host or self._local_source_ip_for(
            self.config.host
        )
        return replace(config, project_socket_host=advertised_host)

    def _local_source_ip_for(self, remote_host: str) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((remote_host, 9))
            return str(sock.getsockname()[0])
        finally:
            sock.close()

    async def _move_to_preset(self, command: int, hold_seconds: float) -> None:
        await self.set_robot_command(command)
        if hold_seconds <= 0:
            return
        deadline = time.monotonic() + hold_seconds
        while time.monotonic() < deadline:
            await self.send_command_heartbeat()
            await asyncio.sleep(0.5)
        await self.stop_command()

    async def _execute_target_move(self, command: int, reset_after: bool) -> None:
        await self.set_robot_command(command)
        if reset_after:
            await self.clear_target_position()
            await self.stop_command()

    def _http_base_url(self) -> str:
        if self.config.origin:
            return self.config.origin.rstrip("/")
        return f"http://{self.config.host}:{self.config.port}"

    async def _http_json(
        self,
        path: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._http_json_sync, path, method, payload, timeout
        )

    def _http_json_sync(
        self,
        path: str,
        method: str,
        payload: Optional[Dict[str, Any]],
        timeout: float,
    ) -> Dict[str, Any]:
        url = urljoin(self._http_base_url() + "/", path.lstrip("/"))
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
        if not text:
            return {}
        return json.loads(text)

    def _node_id(self, prefix: str) -> str:
        return f"{prefix}{uuid.uuid4().hex[:14]}"

    def _default_project_document(
        self,
        project_id: str,
        label: str,
        points: Optional[List[Dict[str, Any]]] = None,
        mark: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build a lean project payload that the controller accepts (minimized from HAR)."""
        task_id = self._node_id("tk")
        points_container_id = self._node_id("po")
        return {
            "children": [
                {
                    "type": "task",
                    "id": task_id,
                    "label": "main1",
                    "uuid": 1,
                    "typeedit": "widgets",
                    "typetask": "main",
                    "parents": [project_id],
                    "children": [
                        {
                            "type": "points",
                            "id": points_container_id,
                            "label": "Points",
                            "uuid": 1,
                            "hideoperate": True,
                            "showchildren": True,
                            "parents": [project_id, task_id],
                            "children": points or [],
                        },
                    ],
                }
            ],
            "ver": "1.6.3c",
            "uuid": 1,
            "type": "project",
            "id": project_id,
            "label": label,
            "hideoperate": True,
            "mark": mark or self._now_ms(),
            "customconfig": {
                "merge": ["points"],
                "robotjoint": 6,
                "robotjointex": 0,
                "robot3d": "axisModel",
            },
            "doing": {"tasks": {"type": "widgets", "main": "", "current": "", "play": []}, "vars": ""},
            "points": True,
        }

    def _get_points_list(self, project: Dict[str, Any]) -> List[Dict[str, Any]]:
        children = project.get("children") or []
        if not children:
            raise ValueError("Project has no task nodes.")
        task = children[0]
        blocks = task.get("children") or []
        for block in blocks:
            if block.get("type") == "points":
                block.setdefault("children", [])
                return block["children"]
        raise ValueError("Points container not found in project document.")

    def _ensure_point_defaults(self, point: Dict[str, Any]) -> Dict[str, Any]:
        payload = copy.deepcopy(point)
        payload.setdefault("type", "point")
        payload.setdefault("icon", "point")
        payload.setdefault("id", self._node_id("pt"))
        payload.setdefault("status", 0)
        payload.setdefault("datatype", "value")
        payload.setdefault("parents", [])
        payload.setdefault("children", [])
        payload.setdefault("candrop", True)
        payload.setdefault("showchildren", True)
        payload.setdefault("attrshow", True)
        return payload

    async def read_project(self, project_id: str) -> Dict[str, Any]:
        """Fetch a project (.crp) file and return the parsed JSON document."""
        resp = await self._http_json(f"/robot/project/read?id={project_id}", method="GET")
        data = resp.get("data") or []
        if not data or "content" not in data[0]:
            raise RuntimeError(
                f"No project content returned for {project_id}.")
        content = data[0]["content"]
        return json.loads(content)

    async def list_projects(self) -> Dict[str, Any]:
        """Return the controller project list payload."""
        return await self._http_json("/robot/project/list", method="GET")

    async def save_project(self, project: Dict[str, Any]) -> Dict[str, Any]:
        """Save a project document via HTTP."""
        return await self._http_json("/robot/project/edit", method="POST", payload=project)

    async def create_project(
        self,
        label: str,
        project_id: Optional[str] = None,
        points: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Create a new empty project on the controller."""
        pid = project_id or self._node_id("pj")
        project = self._default_project_document(pid, label, points=points)
        response = await self.save_project(project)
        return {"id": pid, "label": label, "response": response, "project": project}

    async def upsert_point(
        self,
        project_id: str,
        point: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Add or update a point in a project, then save it."""
        project = await self.read_project(project_id)
        points_list = self._get_points_list(project)
        point_payload = self._ensure_point_defaults(point)
        updated = False
        for idx, existing in enumerate(points_list):
            if existing.get("id") == point_payload["id"]:
                points_list[idx] = point_payload
                updated = True
                break
        if not updated:
            points_list.append(point_payload)
        response = await self.save_project(project)
        return {"response": response, "project": project, "point": point_payload}

    async def delete_point(
        self,
        project_id: str,
        point_id: Optional[str] = None,
        label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remove a point (by id or label) and save the project."""
        if not point_id and not label:
            raise ValueError(
                "point_id or label is required to delete a point.")
        project = await self.read_project(project_id)
        points_list = self._get_points_list(project)
        remaining = []
        removed = []
        for p in points_list:
            if (point_id and p.get("id") == point_id) or (label and p.get("label") == label):
                removed.append(p)
                continue
            remaining.append(p)
        if not removed:
            raise ValueError("No matching point found to delete.")
        points_list[:] = remaining
        response = await self.save_project(project)
        return {"response": response, "project": project, "removed": removed}

    async def delete_project(self, project_id: str) -> Dict[str, Any]:
        """Delete a project via the HTTP endpoint (/robot/project/del?id=...)."""

        def _delete_sync() -> Dict[str, Any]:
            url = urljoin(self._http_base_url() + "/",
                          f"robot/project/del?id={project_id}")
            req = urllib.request.Request(url)
            try:
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    body = resp.read().decode("utf-8", "replace")
                    try:
                        parsed = json.loads(body)
                    except json.JSONDecodeError:
                        parsed = body
                    return {"status": resp.status, "reason": resp.reason, "body": parsed, "raw": body}
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace") if exc.fp else ""
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = body
                return {"status": exc.code, "reason": exc.reason, "body": parsed, "raw": body}

        return await asyncio.to_thread(_delete_sync)

    def _make_message_id(self) -> str:
        return f"ws{uuid.uuid4().hex[:16]}"

    def _make_ws_user_id(self, prefix: str = "ws", extra: int = 0, base: int = 32) -> str:
        seed = self._base36(int(time.time() * 1000))
        if extra > 0:
            seed += self._base36(extra)
        seed = seed[-9:]
        if prefix:
            seed = prefix[-3:] + seed
        while len(seed) < 16:
            seed += self._base_n_digit(base)
        return seed

    def make_ws_user_id(self, prefix: str = "ws", extra: int = 0, base: int = 32) -> str:
        return self._make_ws_user_id(prefix=prefix, extra=extra, base=base)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _base36(value: int) -> str:
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        if value == 0:
            return "0"
        result = ""
        while value > 0:
            value, remainder = divmod(value, 36)
            result = digits[remainder] + result
        return result

    @staticmethod
    def _base_n_digit(base: int) -> str:
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        return digits[random.randint(0, base - 1)]

    @staticmethod
    def _asencode(text: str, key: str) -> str:
        if not text or not key:
            return ""
        key_len = len(key)
        out = []
        for ch in text:
            oidx = int(1000 * random.random() + 0.5) % key_len
            r = int(str(oidx)[-1]) + 1
            base = key_len - r
            codepoint = ord(ch)
            d = codepoint % base
            codepoint = (codepoint - d) // base
            u = codepoint % base
            codepoint = (codepoint - u) // base
            out.append(key[codepoint % base + r])
            out.append(key[u + r])
            out.append(key[d + r])
            out.append(key[oidx])
        return "".join(out)

    @staticmethod
    def _asdecode(text: str, key: str) -> str:
        if not text or not key:
            return ""
        key_len = len(key)
        out = []
        idx = 0
        try:
            while idx + 3 < len(text):
                oidx = key.find(text[idx + 3])
                r = int(str(oidx)[-1]) + 1
                base = key_len - r
                c1 = key.find(text[idx]) - r
                c2 = key.find(text[idx + 1]) - r
                c3 = key.find(text[idx + 2]) - r
                codepoint = c1 * base * base + c2 * base + c3
                out.append(chr(codepoint))
                idx += 4
            return "".join(out)
        except Exception:
            return ""

    def _decode_login_response(self, payload: Dict[str, Any], key: str) -> Optional[Any]:
        data = payload.get("data")
        if not isinstance(data, str):
            return None
        try:
            inner = json.loads(data)
        except json.JSONDecodeError:
            return None
        encoded = inner.get("data")
        if not isinstance(encoded, str):
            return None
        decoded = self._asdecode(encoded, key)
        if not decoded:
            return None
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            return decoded

    @staticmethod
    def _deep_merge(target: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                CodroidAPI._deep_merge(target[key], value)
            else:
                target[key] = value
        return target
