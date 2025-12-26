import asyncio
import copy
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Iterable, Optional

import websockets


@dataclass
class CodroidConfig:
    host: str = "192.168.101.100"
    port: int = 9000
    origin: str = "http://192.168.101.100:9098"
    token: str = "user:admin"
    username: str = "admin"
    usercode: str = (
        "Bm@QI!0@Ask!AsRorI@-sa#v)rI(mmnCai3s)rrmrrg-I!V$!A2fiBM0sa8v!"
        "AdI)reF))vW))JbBB2NAAJoii22iBneI!z6))SzBmKQsabvmmpu!!4h)rYzai"
        "#srIt-AsVKsaBZiBCgiB52ai8p))7WI!K$saMl)r5mBmEirI--I!HVss1UAs"
        "2KBm2TaiJL!AzIsa6lrIr-saVl))KWAs6KaiX7aij7))o()rbWI!45IIo$)r"
        "+FAAX9BByYaa2psaxU!A6IrrTnmm7BII8VAslKBmz+rIMt"
    )
    userwsid: str = "wsmjmnk33nhuji0g"
    ws_user_type: str = "wsuser"
    robot_login_name: str = "web"
    robot_password: str = ""
    robot_ws_type: str = "wsrobot"
    default_language: str = "EN"
    default_project: str = "pjmjbepucimi01gv"
    default_task: str = "tkmjbepuci3lujj8"
    default_label: str = "rumjcr6o3flg6kq0"
    default_stat: int = 2
    default_onlyapi: int = 0
    default_mode: int = 1

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
        headers = {}
        if self.config.origin:
            headers["Origin"] = self.config.origin
        self._ws = await websockets.connect(self.config.ws_url, extra_headers=headers)
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
        return {
            "id": message_id or self._make_message_id(),
            "time": timestamp_ms or self._now_ms(),
            "token": token or self.config.token,
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
        payload = self.build_message(
            message_type="user",
            action="wslogin",
            data={
                "username": username or self.config.username,
                "usercode": usercode or self.config.usercode,
                "userwsid": userwsid or self.config.userwsid,
                "wstype": wstype or self.config.ws_user_type,
            },
        )
        await self.send_message(payload)

    async def robot_login(
        self,
        name: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        wstype: Optional[str] = None,
    ) -> None:
        payload = self.build_message(
            message_type="user",
            action="Login",
            data={
                "name": name or self.config.robot_login_name,
                "password": password if password is not None else self.config.robot_password,
                "username": username or self.config.username,
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
        payload = self.build_message(
            message_type="projexecute",
            action="run",
            data={
                "proj": proj or self.config.default_project,
                "task": task or self.config.default_task,
                "label": label or self.config.default_label,
                "stat": self.config.default_stat if stat is None else stat,
                "onlyapi": self.config.default_onlyapi if onlyapi is None else onlyapi,
                "mode": self.config.default_mode if mode is None else mode,
            },
        )
        await self.send_message(payload)

    async def stop_project(self) -> None:
        payload = self.build_message(message_type="projexecute", action="stop", data={})
        await self.send_message(payload)

    def _make_message_id(self) -> str:
        return f"ws{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _deep_merge(target: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                CodroidAPI._deep_merge(target[key], value)
            else:
                target[key] = value
        return target
