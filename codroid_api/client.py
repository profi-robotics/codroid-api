import asyncio
import copy
import json
import random
import time
import uuid
import urllib.request
from urllib.parse import urljoin
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Optional

import websockets

from codroid_api.commands import (
    RobotCommandSet,
    RobotControlPaths,
    RobotJogMode,
    RobotJogReference,
    RobotTargetPosType,
)

@dataclass
class CodroidConfig:
    """Runtime config for a Codroid websocket connection."""

    # Connection and authentication defaults.
    host: str = "192.168.101.100"
    port: int = 9098
    origin: str = "http://192.168.101.100:9098"
    token: str = "user:admin"
    username: str = "admin"
    user_password: str = "123456"
    usercode: str = ""
    userwsid: str = ""
    ws_user_type: str = "wsuser"
    robot_login_name: str = "web"
    robot_password: str = ""
    robot_ws_type: str = "wsrobot"

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
    jog_references: RobotJogReference = field(default_factory=RobotJogReference)
    target_pos_types: RobotTargetPosType = field(default_factory=RobotTargetPosType)

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/"


class CodroidAPI:
    def __init__(self, config: Optional[CodroidConfig] = None) -> None:
        self.config = config or CodroidConfig()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._recv_task: Optional[asyncio.Task[None]] = None
        self._messages: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

    async def __aenter__(self) -> "CodroidAPI":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._ws is not None:
            return
        connect_kwargs: Dict[str, Any] = {}
        if self.config.origin:
            connect_kwargs["origin"] = self.config.origin
        self._ws = await websockets.connect(self.config.ws_url, **connect_kwargs)
        self._recv_task = asyncio.create_task(self._receiver())

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _receiver(self) -> None:
        if self._ws is None:
            return
        async for message in self._ws:
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                payload = {"raw": message}
            await self._messages.put(payload)

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
        resolved_userwsid = userwsid or self.config.userwsid or self._make_ws_user_id("ws")
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
                json.dumps({"username": resolved_username, "userpass": resolved_password}),
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

    async def read_config(self) -> None:
        payload = self.build_message(message_type="System", action="ReadConfig", data="")
        await self.send_message(payload)

    async def read_system_data(self) -> None:
        payload = self.build_message(message_type="projmanager", action="readsystemdata", data="")
        await self.send_message(payload)

    async def read_global_data(self) -> None:
        payload = self.build_message(message_type="projmanager", action="readglobaldata", data="")
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

    async def set_robot_command(self, command: int) -> None:
        """Send a robot control command code."""
        await self.set_param(self.config.control_paths.command, command)

    async def send_command_heartbeat(self, timestamp_ms: Optional[int] = None) -> None:
        """Send the command heartbeat used by held moves."""
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

    async def power_off(self) -> None:
        """Power off the robot (command code)."""
        await self.set_robot_command(self.config.commands.power_off)

    async def set_manual_mode(self) -> None:
        """Switch to manual mode (command code)."""
        await self.set_robot_command(self.config.commands.manual_mode)

    async def set_auto_mode(self) -> None:
        """Switch to automatic mode (command code)."""
        await self.set_robot_command(self.config.commands.auto_mode)

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
        await self.set_param(self.config.control_paths.target_pos_type, pos_type)

    async def set_target_apos(self, position: Dict[str, Any]) -> None:
        """Set a joint-space target position payload."""
        await self.set_param(self.config.control_paths.target_a_pos, position)

    async def set_target_cpos(self, position: Dict[str, Any]) -> None:
        """Set a cartesian target position payload."""
        await self.set_param(self.config.control_paths.target_c_pos, position)

    async def clear_target_position(self) -> None:
        """Clear the active target position selection."""
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

    async def set_current_coordinate_id(self, coordinate_id: int) -> None:
        """Select the active coordinate system."""
        payload = self.build_message(
            message_type="Robot",
            action="SetCurrentCoordinateId",
            data=coordinate_id,
        )
        await self.send_message(payload)

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
        payload = self.build_message(message_type="trajectory", action="getRecordFlag", data="")
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
        payload = self.build_message(message_type="common", action="getLogFileList", data={})
        await self.send_message(payload)

    async def get_io_info(self) -> None:
        payload = self.build_message(message_type="IOManager", action="GetIOInfo", data="")
        await self.send_message(payload)

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
            raise ValueError("Project id is required; set CODROID_DEFAULT_PROJECT.")
        if not resolved_task:
            raise ValueError("Task id is required; set CODROID_DEFAULT_TASK.")
        if not resolved_label:
            raise ValueError("Label id is required; set CODROID_DEFAULT_LABEL.")
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
        payload = self.build_message(message_type="projexecute", action="stop", data={})
        await self.send_message(payload)

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
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
        if not text:
            return {}
        return json.loads(text)

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
