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
    ) -> None:
        self._settings = settings or CodroidSettings()
        self._primary_flange_port = flange_button_port
        if flange_button_ports is None:
            flange_button_ports = (flange_button_port,)
        self._flange_button_ports = tuple(flange_button_ports)
        self._io_poll_interval = io_poll_interval
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_lock = threading.Lock()
        self._position_lock = threading.Lock()
        self._press_lock = threading.Lock()
        self._di_lock = threading.Lock()
        self._position = RobotPosture()
        self._posture_seen = False
        self._last_press_timestamp: Optional[float] = None
        self._press_count = 0
        self._di_state: Dict[int, int] = {}

        self.user_api: Optional[CodroidAPI] = None
        self.user_uri: Optional[str] = None
        self.user_listener_task: Optional[asyncio.Task[None]] = None
        self.robot_api: Optional[CodroidAPI] = None
        self.robot_uri: Optional[str] = None
        self.robot_listener_task: Optional[asyncio.Task[None]] = None
        self.robot_poll_task: Optional[asyncio.Task[None]] = None
        self.robot_press_queue: Optional[asyncio.Queue[Dict[str, Any]]] = None

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

    def clear_position(self) -> None:
        with self._position_lock:
            self._position = RobotPosture()
            self._posture_seen = False
        with self._press_lock:
            self._last_press_timestamp = None
            self._press_count = 0
        with self._di_lock:
            self._di_state = {}

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

    def is_connected(self, robot_uri: str) -> bool:
        resolved_uri = robot_uri or self.default_robot_uri()
        return self.robot_api is not None and self.robot_uri == resolved_uri

    def default_robot_uri(self) -> str:
        return f"ws://{self._settings.host}:{self._settings.robot_port}/"

    def resolve_robot_target(self, robot_uri: str) -> Dict[str, Any]:
        settings = self._apply_robot_uri(robot_uri or self.default_robot_uri())
        return {"host": settings.host, "port": settings.robot_port}

    async def connect(self, robot_uri: str) -> CodroidAPI:
        return await self._run_on_loop(self._connect_on_loop(robot_uri))

    async def close(self) -> None:
        await self._run_on_loop(self._close_on_loop())

    async def probe(self, robot_uri: str) -> Dict[str, Any]:
        async def _runner() -> Dict[str, Any]:
            robot_api = await self._connect_on_loop(robot_uri)
            if hasattr(robot_api, "read_system_data"):
                await robot_api.read_system_data()
            return self.resolve_robot_target(robot_uri)

        return await self._run_on_loop(_runner())

    async def run_with_robot(
        self,
        robot_uri: str,
        action: Callable[[CodroidAPI], Awaitable[T]],
    ) -> T:
        async def _runner() -> T:
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
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.user_listener_task = None

    async def _ensure_user_connection_on_loop(self, settings: CodroidSettings) -> None:
        user_config = settings.build_user_config()
        user_uri = f"ws://{settings.host}:{settings.ws_port}/"
        if not user_config.userwsid:
            user_config.userwsid = f"ws{uuid.uuid4().hex[:12]}"

        if self.user_api is not None and self.user_uri == user_uri:
            return

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
        config = user_api.config
        payload = {
            "username": config.username,
            "usercode": config.usercode,
            "userwsid": config.userwsid,
            "wstype": config.ws_user_type,
        }
        for action in ("wslogout", "logout", "Logout"):
            try:
                message = user_api.build_message(
                    message_type="user",
                    action=action,
                    data=payload,
                )
                await user_api.send_message(message)
                LOGGER.debug("Sent user logout message: %s", action)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("User logout action %s failed: %s", action, exc)

    async def _simulate_robot_logout(self, robot_api: CodroidAPI) -> None:
        config = robot_api.config
        payload = {
            "name": config.robot_login_name,
            "password": config.robot_password,
            "username": config.username,
            "wstype": config.robot_ws_type,
        }
        for action in ("Logout", "logout"):
            try:
                message = robot_api.build_message(
                    message_type="user",
                    action=action,
                    data=payload,
                )
                await robot_api.send_message(message)
                LOGGER.debug("Sent robot logout message: %s", action)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Robot logout action %s failed: %s", action, exc)

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
        await self._ensure_user_connection_on_loop(settings)

        if self.robot_api is not None and self.robot_uri == resolved_uri:
            LOGGER.debug(
                "Reusing existing robot connection to %s", resolved_uri)
            await self._ensure_robot_monitor(self.robot_api)
            return self.robot_api

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
        LOGGER.info("Persistent robot connection established")
        return robot_api

    async def _close_on_loop(self) -> None:
        if self.user_api is not None:
            LOGGER.info("Closing persistent user connection")
            await self._simulate_user_logout(self.user_api)
            await self._stop_user_monitor()
            try:
                await self.user_api.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Error closing user connection: %s", exc)
            self.user_api = None
            self.user_uri = None

        if self.robot_api is not None:
            LOGGER.info("Closing persistent robot connection")
            await self._simulate_robot_logout(self.robot_api)
            await self._stop_robot_monitor()
            try:
                await self.robot_api.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Error closing robot connection: %s", exc)
            self.robot_api = None
            self.robot_uri = None
            self.clear_position()

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
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("Robot listener error: %s", exc, exc_info=True)

        async def _poll_io() -> None:
            LOGGER.debug("Robot IO poll task started.")
            while True:
                request = robot_api.build_message(
                    message_type="IOManager",
                    action="GetIOValue",
                    data=list(self._flange_button_ports),
                )
                await robot_api.send_message(request)
                await asyncio.sleep(self._io_poll_interval)

        if self.robot_listener_task is None or self.robot_listener_task.done():
            self.robot_listener_task = asyncio.create_task(_listener())
        if self.robot_poll_task is None or self.robot_poll_task.done():
            self.robot_poll_task = asyncio.create_task(_poll_io())

    async def _stop_robot_monitor(self) -> None:
        tasks = [self.robot_listener_task, self.robot_poll_task]
        for task in tasks:
            if task is None or task.done():
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.robot_listener_task = None
        self.robot_poll_task = None
        self.robot_press_queue = None
