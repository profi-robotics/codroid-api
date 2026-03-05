from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, TypeVar
from urllib.parse import urlparse

from codroid_api.client import CodroidAPI
from codroid_api.settings import CodroidSettings
from websockets.exceptions import ConnectionClosed

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass
class RobotPosture:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    a: float = 0.0
    b: float = 0.0
    c: float = 0.0
    timestamp: float = 0.0

    def age_seconds(self) -> float:
        return time.time() - self.timestamp


class RobotSession:
    """Persistent robot/user websocket session with monitoring helpers."""

    def __init__(
        self,
        settings: Optional[CodroidSettings] = None,
        *,
        flange_button_port: int = 41,
        flange_button_ports: Optional[Iterable[int]] = None,
        io_poll_interval: float = 0.2,
        release_grace_period_s: float = 0.5,
    ) -> None:
        self._settings = settings or CodroidSettings()
        self._primary_flange_port = flange_button_port
        if flange_button_ports is None:
            flange_button_ports = (flange_button_port,)
        self._flange_button_ports = tuple(flange_button_ports)
        self._io_poll_interval = io_poll_interval
        self._release_grace_period_s = max(0.0, release_grace_period_s)
        self._acquire_not_before: float = 0.0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_lock = threading.Lock()
        self._position_lock = threading.Lock()
        self._press_lock = threading.Lock()
        self._di_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._coordinate_lock = threading.Lock()
        self._calibration_lock = threading.Lock()
        self._warning_lock = threading.Lock()
        self._error_lock = threading.Lock()
        self._position = RobotPosture()
        self._posture_seen = False
        self._last_press_timestamp: Optional[float] = None
        self._press_count = 0
        self._di_state: Dict[int, int] = {}
        self._robot_status: Dict[str, Any] = {}
        self._robot_status_timestamp: Optional[float] = None
        self._robot_coordinate: Dict[str, Any] = {}
        self._robot_coordinate_timestamp: Optional[float] = None
        self._robot_calibration_frame: Dict[str, Any] = {}
        self._robot_calibration_timestamp: Optional[float] = None
        self._robot_warning: Dict[str, Any] = {}
        self._robot_warning_timestamp: Optional[float] = None
        self._robot_error: Dict[str, Any] = {}
        self._robot_error_timestamp: Optional[float] = None
        self._emergency_button_active = False

        self.user_api: Optional[CodroidAPI] = None
        self.user_uri: Optional[str] = None
        self.user_listener_task: Optional[asyncio.Task[None]] = None
        self.robot_api: Optional[CodroidAPI] = None
        self.robot_uri: Optional[str] = None
        self.robot_listener_task: Optional[asyncio.Task[None]] = None
        self.robot_poll_task: Optional[asyncio.Task[None]] = None
        self.robot_press_queue: Optional[asyncio.Queue[Dict[str, Any]]] = None
        self._control_mode: str = "acquired"
        self._released_at: float = 0.0
        self._last_release_error: Optional[str] = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            self._loop = asyncio.new_event_loop()

            def _run_loop() -> None:
                asyncio.set_event_loop(self._loop)
                self._loop.run_forever()

            self._loop_thread = threading.Thread(
                target=_run_loop, name="CodroidRobotLoop", daemon=True
            )
            self._loop_thread.start()
        return self._loop

    def _is_loop(self) -> bool:
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    async def _run_on_loop(self, coro: Awaitable[T]) -> T:
        if self._is_loop():
            return await coro
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return await asyncio.wrap_future(future)

    def _apply_robot_uri(self, uri: str) -> CodroidSettings:
        if not uri:
            return self._settings
        parsed = urlparse(uri)
        if not parsed.hostname and not parsed.port:
            return self._settings
        if hasattr(self._settings, "model_copy"):
            settings = self._settings.model_copy()
        else:
            settings = self._settings.copy()
        if parsed.hostname:
            settings.host = parsed.hostname
        if parsed.port:
            settings.robot_port = parsed.port
        return settings

    @staticmethod
    def _api_is_open(api: Optional[CodroidAPI]) -> bool:
        """Return True when the underlying websocket appears open."""
        if api is None:
            return False
        ws = getattr(api, "_ws", None)
        if ws is None:
            return False
        try:
            closed = getattr(ws, "closed", None)
            if isinstance(closed, bool):
                return not closed
        except Exception:
            pass
        state = getattr(ws, "state", None)
        if state is not None:
            state_text = str(state).lower()
            if "open" in state_text:
                return True
            if "closed" in state_text:
                return False
        close_code = getattr(ws, "close_code", None)
        return close_code is None

    def clear_position(self) -> None:
        with self._position_lock:
            self._position = RobotPosture()
            self._posture_seen = False
        with self._press_lock:
            self._last_press_timestamp = None
            self._press_count = 0
        with self._di_lock:
            self._di_state = {}
        with self._status_lock:
            self._robot_status = {}
            self._robot_status_timestamp = None
        with self._coordinate_lock:
            self._robot_coordinate = {}
            self._robot_coordinate_timestamp = None
        with self._warning_lock:
            self._robot_warning = {}
            self._robot_warning_timestamp = None
            self._emergency_button_active = False
        with self._error_lock:
            self._robot_error = {}
            self._robot_error_timestamp = None
        with self._calibration_lock:
            self._robot_calibration_frame = {}
            self._robot_calibration_timestamp = None

    def posture_seen(self) -> bool:
        with self._position_lock:
            return self._posture_seen

    def position_snapshot(self) -> RobotPosture:
        with self._position_lock:
            return replace(self._position)

    def flange_press_age_seconds(self) -> Optional[float]:
        with self._press_lock:
            if self._last_press_timestamp is None:
                return None
            return time.time() - self._last_press_timestamp

    def flange_pressed_recently(self, window_s: float = 1.0) -> bool:
        age = self.flange_press_age_seconds()
        return age is not None and age <= window_s

    def flange_button_states(self) -> Dict[int, int]:
        with self._di_lock:
            return {
                port: int(self._di_state.get(port, 0))
                for port in self._flange_button_ports
            }

    def robot_status_snapshot(self) -> Dict[str, Any]:
        with self._status_lock:
            return dict(self._robot_status)

    def robot_status_age_seconds(self) -> Optional[float]:
        with self._status_lock:
            if self._robot_status_timestamp is None:
                return None
            return time.time() - self._robot_status_timestamp

    def robot_warning_snapshot(self) -> Dict[str, Any]:
        with self._warning_lock:
            return dict(self._robot_warning)

    def robot_warning_age_seconds(self) -> Optional[float]:
        with self._warning_lock:
            if self._robot_warning_timestamp is None:
                return None
            return time.time() - self._robot_warning_timestamp

    def robot_error_snapshot(self) -> Dict[str, Any]:
        with self._error_lock:
            return dict(self._robot_error)

    def robot_error_age_seconds(self) -> Optional[float]:
        with self._error_lock:
            if self._robot_error_timestamp is None:
                return None
            return time.time() - self._robot_error_timestamp

    def emergency_button_active(self) -> bool:
        with self._warning_lock:
            return bool(self._emergency_button_active)

    def robot_power_on(self) -> Optional[bool]:
        status = self.robot_status_snapshot()
        if not status:
            return None
        power_keys = (
            "PowerOn",
            "Power",
            "power",
            "servo",
            "Servo",
            "servoOn",
            "ServoOn",
        )
        true_values = {"1", "true", "on", "enabled", "poweron", "servoon"}
        false_values = {"0", "false", "off",
                        "disabled", "poweroff", "servooff"}

        def _coerce_value(value: Any) -> Optional[bool]:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in true_values:
                    return True
                if lowered in false_values:
                    return False
                return None
            if isinstance(value, dict):
                if "value" in value:
                    return _coerce_value(value.get("value"))
            return None

        def _search_payload(payload: Dict[str, Any]) -> Optional[bool]:
            for key in power_keys:
                if key in payload:
                    coerced = _coerce_value(payload.get(key))
                    if coerced is not None:
                        return coerced
            for key, value in payload.items():
                if isinstance(key, str):
                    lowered = key.lower()
                    if "power" in lowered or "servo" in lowered:
                        coerced = _coerce_value(value)
                        if coerced is not None:
                            return coerced
                if isinstance(value, dict):
                    coerced = _search_payload(value)
                    if coerced is not None:
                        return coerced
            return None

        return _search_payload(status)

    def coordinate_frame_snapshot(self) -> Dict[str, Any]:
        with self._coordinate_lock:
            return dict(self._robot_coordinate)

    def coordinate_frame_timestamp(self) -> Optional[float]:
        with self._coordinate_lock:
            return self._robot_coordinate_timestamp

    def calibration_frame_snapshot(self) -> Dict[str, Any]:
        with self._calibration_lock:
            return dict(self._robot_calibration_frame)

    def calibration_frame_timestamp(self) -> Optional[float]:
        with self._calibration_lock:
            return self._robot_calibration_timestamp

    def is_connected(self, robot_uri: str) -> bool:
        resolved_uri = robot_uri or self.default_robot_uri()
        return (
            self.robot_api is not None
            and self.robot_uri == resolved_uri
            and self._api_is_open(self.robot_api)
        )

    def default_robot_uri(self) -> str:
        return f"ws://{self._settings.host}:{self._settings.robot_port}/"

    def resolve_robot_target(self, robot_uri: str) -> Dict[str, Any]:
        settings = self._apply_robot_uri(robot_uri or self.default_robot_uri())
        return {"host": settings.host, "port": settings.robot_port}

    def control_state(self) -> Dict[str, Any]:
        return {
            "mode": self._control_mode,
            "released_at": float(self._released_at),
            "last_error": self._last_release_error,
        }

    async def connect(self, robot_uri: str) -> CodroidAPI:
        return await self._run_on_loop(self._connect_on_loop(robot_uri))

    async def acquire_control(
        self,
        robot_uri: str,
        *,
        force_reconnect: bool = False,
    ) -> CodroidAPI:
        return await self._run_on_loop(
            self._acquire_control_on_loop(robot_uri, force_reconnect=force_reconnect)
        )

    async def release_control(
        self,
        robot_uri: str,
        *,
        power_off: bool = True,
        wait_closed_s: float = 2.0,
    ) -> Dict[str, Any]:
        return await self._run_on_loop(
            self._release_control_on_loop(
                robot_uri,
                power_off=power_off,
                wait_closed_s=wait_closed_s,
            )
        )

    async def close(self) -> None:
        await self._run_on_loop(self._close_on_loop())

    async def probe(self, robot_uri: str, *, require_acquired: bool = False) -> Dict[str, Any]:
        async def _runner() -> Dict[str, Any]:
            if require_acquired and self._control_mode == "released":
                raise RuntimeError("Robot control is released; acquire control first.")
            robot_api = await self._connect_on_loop(robot_uri)
            if hasattr(robot_api, "read_system_data"):
                await robot_api.read_system_data()
            return self.resolve_robot_target(robot_uri)

        return await self._run_on_loop(_runner())

    async def run_with_robot(
        self,
        robot_uri: str,
        action: Callable[[CodroidAPI], Awaitable[T]],
        *,
        require_acquired: bool = False,
    ) -> T:
        async def _runner() -> T:
            if require_acquired and self._control_mode == "released":
                raise RuntimeError("Robot control is released; acquire control first.")
            robot_api = await self._connect_on_loop(robot_uri)
            return await action(robot_api)

        return await self._run_on_loop(_runner())

    async def reset_flange_queue(self) -> None:
        async def _runner() -> None:
            self.robot_press_queue = asyncio.Queue()

        await self._run_on_loop(_runner())

    async def next_flange_press(self, timeout: float) -> Dict[str, Any]:
        async def _runner() -> Dict[str, Any]:
            if self.robot_press_queue is None:
                self.robot_press_queue = asyncio.Queue()
            return await asyncio.wait_for(self.robot_press_queue.get(), timeout=timeout)

        return await self._run_on_loop(_runner())

    async def _ensure_user_monitor(self, user_api: CodroidAPI) -> None:
        async def _listener() -> None:
            try:
                async for _msg in user_api.listen():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("User listener error: %s", exc)

        if self.user_listener_task is None or self.user_listener_task.done():
            self.user_listener_task = asyncio.create_task(_listener())

    async def _stop_user_monitor(self) -> None:
        task = self.user_listener_task
        if task is not None and task.done():
            with contextlib.suppress(Exception):
                task.exception()
        elif task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.user_listener_task = None

    async def _ensure_user_connection_on_loop(self, settings: CodroidSettings) -> None:
        user_config = settings.build_user_config()
        user_uri = f"ws://{settings.host}:{settings.ws_port}/"
        if not user_config.userwsid:
            user_config.userwsid = f"ws{uuid.uuid4().hex[:12]}"

        if (
            self.user_api is not None
            and self.user_uri == user_uri
            and self._api_is_open(self.user_api)
        ):
            return

        if (
            self.user_api is not None
            and self.user_uri == user_uri
            and not self._api_is_open(self.user_api)
        ):
            await self._stop_user_monitor()
            try:
                await self.user_api.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Error closing stale user connection: %s", exc)
            self.user_api = None
            self.user_uri = None

        if self.user_api is not None and self.user_uri != user_uri:
            await self._stop_user_monitor()
            try:
                await self.user_api.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Error closing user connection: %s", exc)
            self.user_api = None
            self.user_uri = None

        LOGGER.info("Creating persistent user connection to %s", user_uri)
        user_api = CodroidAPI(user_config)
        await user_api.__aenter__()
        try:
            if user_config.user_password:
                await user_api.ws_login_with_password()
            elif user_config.usercode:
                await user_api.ws_login()
            else:
                LOGGER.debug("User credentials not set; skipping ws_login")
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("User websocket login failed: %s", exc)
        else:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    message = await user_api.recv(timeout=1.0)
                except TimeoutError:
                    continue
                if message.get("action") == "online":
                    break

        self.user_api = user_api
        self.user_uri = user_uri
        await self._ensure_user_monitor(user_api)

    async def _simulate_user_logout(self, user_api: CodroidAPI) -> None:
        for action in ("wslogout", "logout", "Logout"):
            try:
                message = user_api.build_user_logout_message(action=action)
                await user_api.send_message(message)
                LOGGER.debug("Sent user logout message: %s", action)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("User logout action %s failed: %s", action, exc)

    async def _simulate_robot_logout(self, robot_api: CodroidAPI) -> None:
        for action in ("Logout", "logout"):
            try:
                message = robot_api.build_robot_logout_message(action=action)
                await robot_api.send_message(message)
                LOGGER.debug("Sent robot logout message: %s", action)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Robot logout action %s failed: %s", action, exc)

    async def _verify_closed(
        self,
        *,
        user_api: Optional[CodroidAPI],
        robot_api: Optional[CodroidAPI],
        wait_closed_s: float,
    ) -> None:
        deadline = time.monotonic() + max(0.0, wait_closed_s)
        while time.monotonic() <= deadline:
            user_open = self._api_is_open(user_api)
            robot_open = self._api_is_open(robot_api)
            if not user_open and not robot_open:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError(
            "Websocket close verification failed "
            f"(user_open={self._api_is_open(user_api)}, robot_open={self._api_is_open(robot_api)})."
        )

    async def _release_connections_on_loop(
        self,
        *,
        resolved_uri: str,
        power_off: bool,
        wait_closed_s: float,
        mark_released: bool,
    ) -> Dict[str, Any]:
        started = time.monotonic()
        LOGGER.info(
            "control_release_started uri=%s power_off=%s",
            resolved_uri,
            power_off,
        )

        user_api = self.user_api
        robot_api = self.robot_api
        failed_step = ""
        try:
            if robot_api is not None and self._api_is_open(robot_api):
                failed_step = "stop_command"
                with contextlib.suppress(Exception):
                    await robot_api.stop_command()
                if power_off:
                    failed_step = "power_off"
                    with contextlib.suppress(Exception):
                        await robot_api.power_off()

            failed_step = "logout"
            if user_api is not None and self._api_is_open(user_api):
                await self._simulate_user_logout(user_api)
            if robot_api is not None and self._api_is_open(robot_api):
                await self._simulate_robot_logout(robot_api)

            failed_step = "stop_monitors"
            await self._stop_user_monitor()
            await self._stop_robot_monitor()

            failed_step = "close_connections"
            if user_api is not None:
                with contextlib.suppress(Exception):
                    await user_api.__aexit__(None, None, None)
            if robot_api is not None:
                with contextlib.suppress(Exception):
                    await robot_api.__aexit__(None, None, None)

            failed_step = "verify_closed"
            await self._verify_closed(
                user_api=user_api,
                robot_api=robot_api,
                wait_closed_s=wait_closed_s,
            )

            self.user_api = None
            self.user_uri = None
            self.robot_api = None
            self.robot_uri = None
            self.clear_position()

            if mark_released:
                self._control_mode = "released"
                self._released_at = time.time()
                self._last_release_error = None
                self._acquire_not_before = time.monotonic() + self._release_grace_period_s

            elapsed_ms = int((time.monotonic() - started) * 1000)
            LOGGER.info(
                "control_release_completed uri=%s elapsed_ms=%s mode=%s",
                resolved_uri,
                elapsed_ms,
                self._control_mode,
            )
            return self.control_state()
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._last_release_error = str(exc)
            if mark_released:
                self._control_mode = "acquired"
                self._released_at = 0.0
            LOGGER.error(
                "control_release_failed uri=%s elapsed_ms=%s step=%s error=%s",
                resolved_uri,
                elapsed_ms,
                failed_step or "unknown",
                exc,
            )
            raise

    async def _prime_robot_stream(self, robot_api: CodroidAPI) -> None:
        for call, label in (
            (robot_api.read_config, "read_config"),
            (robot_api.read_system_data, "read_system_data"),
            (robot_api.read_global_data, "read_global_data"),
            (robot_api.get_io_info, "get_io_info"),
            (robot_api.set_language, "set_language"),
        ):
            try:
                await call()
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Robot stream priming %s failed: %s", label, exc)

    async def _connect_on_loop(self, robot_uri: str) -> CodroidAPI:
        resolved_uri = robot_uri or self.default_robot_uri()
        settings = self._apply_robot_uri(resolved_uri)

        if self.robot_api is not None and self.robot_uri == resolved_uri:
            if self._api_is_open(self.robot_api):
                await self._ensure_robot_monitor(self.robot_api)
                self._control_mode = "acquired"
                self._released_at = 0.0
                self._last_release_error = None
                return self.robot_api
            LOGGER.info(
                "Detected stale robot websocket for %s; reconnecting.",
                resolved_uri,
            )
            await self._stop_robot_monitor()
            try:
                await self.robot_api.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Error closing stale robot connection: %s", exc)
            self.robot_api = None

        if self.robot_api is not None and self.robot_uri != resolved_uri:
            LOGGER.info(
                "Switching robot connection from %s to %s",
                self.robot_uri,
                resolved_uri,
            )
            await self._stop_robot_monitor()
            try:
                await self.robot_api.__aexit__(None, None, None)
            except Exception:
                pass
            self.robot_api = None

        await self._ensure_user_connection_on_loop(settings)

        LOGGER.info("Creating persistent robot connection to %s", resolved_uri)
        robot_config = settings.build_robot_config()
        robot_api = CodroidAPI(robot_config)
        await robot_api.__aenter__()
        await robot_api.robot_login()
        await self._prime_robot_stream(robot_api)

        self.robot_api = robot_api
        self.robot_uri = resolved_uri
        self.clear_position()
        await self._ensure_robot_monitor(robot_api)
        self._control_mode = "acquired"
        self._released_at = 0.0
        self._last_release_error = None
        LOGGER.info("Persistent robot connection established")
        return robot_api

    async def _acquire_control_on_loop(
        self,
        robot_uri: str,
        *,
        force_reconnect: bool = False,
    ) -> CodroidAPI:
        resolved_uri = robot_uri or self.default_robot_uri()
        now = time.monotonic()
        if now < self._acquire_not_before:
            await asyncio.sleep(self._acquire_not_before - now)

        if force_reconnect:
            await self._release_connections_on_loop(
                resolved_uri=resolved_uri,
                power_off=False,
                wait_closed_s=2.0,
                mark_released=False,
            )

        robot_api = await self._connect_on_loop(resolved_uri)
        self._control_mode = "acquired"
        self._released_at = 0.0
        self._last_release_error = None
        return robot_api

    async def _release_control_on_loop(
        self,
        robot_uri: str,
        *,
        power_off: bool,
        wait_closed_s: float,
    ) -> Dict[str, Any]:
        resolved_uri = robot_uri or self.default_robot_uri()
        return await self._release_connections_on_loop(
            resolved_uri=resolved_uri,
            power_off=power_off,
            wait_closed_s=wait_closed_s,
            mark_released=True,
        )

    async def _close_on_loop(self) -> None:
        try:
            await self._release_connections_on_loop(
                resolved_uri=self.robot_uri or self.default_robot_uri(),
                power_off=False,
                wait_closed_s=2.0,
                mark_released=False,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Error closing persistent connections: %s", exc)

    async def _ensure_robot_monitor(self, robot_api: CodroidAPI) -> None:
        if self.robot_press_queue is None:
            self.robot_press_queue = asyncio.Queue()

        async def _listener() -> None:
            latest_posture: Dict[str, Any] = {}
            di_state: Dict[int, int] = {}
            LOGGER.info("Listener task started, waiting for robot messages...")
            try:
                async for msg in robot_api.listen():
                    action = msg.get("action")
                    msg_type = msg.get("type")
                    if action == "RobotPosture":
                        data = (msg.get("data") or {}).get("data") or {}
                        latest_posture = data.get("end") or latest_posture
                        with self._position_lock:
                            self._posture_seen = True
                            self._position = RobotPosture(
                                x=float(latest_posture.get("x", 0.0)),
                                y=float(latest_posture.get("y", 0.0)),
                                z=float(latest_posture.get("z", 0.0)),
                                a=float(latest_posture.get("a", 0.0)),
                                b=float(latest_posture.get("b", 0.0)),
                                c=float(latest_posture.get("c", 0.0)),
                                timestamp=time.time(),
                            )
                    if action == "RobotStatus":
                        status_payload = (
                            msg.get("data") or {}).get("data") or {}
                        status = status_payload.get("data") or status_payload
                        if isinstance(status, dict):
                            with self._status_lock:
                                self._robot_status = dict(status)
                                self._robot_status_timestamp = time.time()
                    if action == "RobotWarning":
                        with self._warning_lock:
                            self._robot_warning = dict(msg)
                            self._robot_warning_timestamp = time.time()
                            if CodroidAPI.is_emergency_button_warning(msg):
                                self._emergency_button_active = True
                            elif (
                                self._emergency_button_active
                                and CodroidAPI.is_robot_warning_cleared(msg)
                            ):
                                self._emergency_button_active = False
                    if action == "RobotError":
                        with self._error_lock:
                            self._robot_error = dict(msg)
                            self._robot_error_timestamp = time.time()
                    if action == "RobotCoordinate":
                        data = (msg.get("data") or {}).get("data") or {}
                        frame = data.get("user") or data
                        if isinstance(frame, dict):
                            with self._coordinate_lock:
                                self._robot_coordinate = dict(frame)
                                self._robot_coordinate_timestamp = time.time()
                    if msg_type == "Robot" and action == "CoordinateCalibration":
                        payload = (msg.get("data") or {}).get("data") or {}
                        if isinstance(payload, dict) and payload:
                            with self._calibration_lock:
                                self._robot_calibration_frame = dict(payload)
                                self._robot_calibration_timestamp = time.time()
                    # LOGGER.debug(
                    #     "RobotPosture: x=%.1f, y=%.1f, z=%.1f",
                    #     latest_posture.get("x", 0.0),
                    #     latest_posture.get("y", 0.0),
                    #     latest_posture.get("z", 0.0),
                        # )
                    di = CodroidAPI.extract_di_state(msg)
                    if di:
                        with self._di_lock:
                            self._di_state.update(
                                {int(port): int(value)
                                 for port, value in di.items()}
                            )
                    if di and self._primary_flange_port in di:
                        prev = di_state.get(self._primary_flange_port)
                        di_state[self._primary_flange_port] = di[
                            self._primary_flange_port
                        ]
                        if (
                            prev != di[self._primary_flange_port]
                            and di[self._primary_flange_port] == 1
                        ):
                            queue = self.robot_press_queue
                            if latest_posture and queue is not None:
                                LOGGER.info(
                                    "Flange button pressed with posture: %s",
                                    latest_posture,
                                )
                                with self._press_lock:
                                    self._last_press_timestamp = time.time()
                                    self._press_count += 1
                                queue.put_nowait(latest_posture.copy())
                            else:
                                LOGGER.warning(
                                    "Flange button pressed but no posture data available"
                                )
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                LOGGER.debug("Robot listener closed: %s", exc)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("Robot listener error: %s", exc, exc_info=True)

        async def _poll_io() -> None:
            LOGGER.debug("Robot IO poll task started.")
            try:
                while True:
                    request = robot_api.build_message(
                        message_type="IOManager",
                        action="GetIOValue",
                        data=list(self._flange_button_ports),
                    )
                    await robot_api.send_message(request)
                    await asyncio.sleep(self._io_poll_interval)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                LOGGER.debug("Robot IO poll task stopped (connection closed): %s", exc)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Robot IO poll task stopped: %s", exc)

        if self.robot_listener_task is None or self.robot_listener_task.done():
            self.robot_listener_task = asyncio.create_task(_listener())
        if self.robot_poll_task is None or self.robot_poll_task.done():
            self.robot_poll_task = asyncio.create_task(_poll_io())

    async def _stop_robot_monitor(self) -> None:
        tasks = [self.robot_listener_task, self.robot_poll_task]
        for task in tasks:
            if task is None:
                continue
            if task.done():
                with contextlib.suppress(Exception):
                    task.exception()
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.robot_listener_task = None
        self.robot_poll_task = None
        self.robot_press_queue = None
