from __future__ import annotations

import asyncio
import contextlib
import copy
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


JsonDict = Dict[str, Any]
NodeIdFactory = Callable[[str], str]

DEFAULT_AUTO_SOCKET_PROJECT_ID = "pjcodroidautosock"
DEFAULT_AUTO_SOCKET_TASK_ID = "tkcodroidautosock"
DEFAULT_AUTO_SOCKET_RUN_LABEL_ID = "sccodroidautosock"


@dataclass(frozen=True)
class AutoSocketConfig:
    """Configuration for Codroid auto-mode socket movement."""

    socket_role: str = "server"
    socket_host: Optional[str] = None
    socket_bind_host: str = "0.0.0.0"
    socket_port: int = 8080
    project_socket_host: Optional[str] = None
    project_socket_port: int = 8080
    project_id: Optional[str] = DEFAULT_AUTO_SOCKET_PROJECT_ID
    task_id: Optional[str] = DEFAULT_AUTO_SOCKET_TASK_ID
    run_label_id: Optional[str] = DEFAULT_AUTO_SOCKET_RUN_LABEL_ID
    project_label: str = "CodroidAutoSocketMode"
    connect_timeout_s: float = 3.0
    connect_attempts: int = 3
    connect_retry_delay_s: float = 0.25
    accept_timeout_s: float = 10.0
    write_timeout_s: float = 3.0
    read_timeout_ms: int = 2000
    run_stat: int = 2
    run_onlyapi: int = 0
    run_mode: int = 1
    move_speed: str = "V100"
    move_acc: str = "ACC100"
    move_zone: str = "ZONE0"
    move_blend_type: str = "FINE"
    frame_start: str = "["
    frame_end: str = "]"
    frame_separator: str = ","
    append_newline: bool = False
    install_project: bool = False
    start_project: bool = False
    stop_project_on_exit: bool = False
    connect_on_enter: bool = True


@dataclass(frozen=True)
class AutoSocketProjectInfo:
    """Identifiers needed to run a generated auto socket project."""

    project_id: str
    label: str
    task_id: str
    run_label_id: str
    socket_host: str
    socket_port: int
    project: Optional[JsonDict] = None


class AutoSocketBackend:
    """TCP writer used by CodroidAPI while auto socket mode is active."""

    def __init__(
        self,
        config: AutoSocketConfig,
        *,
        default_host: str,
        linear_command: int = 106,
    ) -> None:
        self.config = config
        self.default_host = default_host
        self.linear_command = linear_command
        self.target_pos_type: Optional[int] = None
        self.target_cpos: Optional[JsonDict] = None
        self.target_apos: Optional[JsonDict] = None
        self.project_info: Optional[AutoSocketProjectInfo] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._accepted_clients: asyncio.Queue[
            tuple[asyncio.StreamReader, asyncio.StreamWriter]
        ] = asyncio.Queue()

    @property
    def host(self) -> str:
        return self.config.socket_host or self.default_host

    @property
    def port(self) -> int:
        return self.config.socket_port

    def is_connected(self) -> bool:
        """Return True when the controller TCP client is currently connected."""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Open or accept the TCP connection used by the socket project."""
        if self.config.socket_role.lower() == "server":
            await self._accept_connection()
            return
        if self.config.socket_role.lower() != "client":
            raise ValueError("Auto socket role must be 'server' or 'client'.")
        await self._open_client_connection()

    async def _open_client_connection(self) -> None:
        if self._writer is not None and not self._writer.is_closing():
            return
        last_error: Optional[BaseException] = None
        attempts = max(1, self.config.connect_attempts)
        for attempt in range(attempts):
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.config.connect_timeout_s,
                )
                return
            except (asyncio.TimeoutError, OSError, ConnectionError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(self.config.connect_retry_delay_s)
        if last_error is not None:
            raise last_error

    async def _start_server(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            self.config.socket_bind_host,
            self.port,
        )

    async def start_listening(self) -> None:
        """Bind the local server socket without waiting for a client."""
        role = self.config.socket_role.lower()
        if role == "server":
            await self._start_server()
            return
        if role != "client":
            raise ValueError("Auto socket role must be 'server' or 'client'.")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await self._accepted_clients.put((reader, writer))
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError, RuntimeError):
            pass

    async def _accept_connection(self) -> None:
        if self._writer is not None and not self._writer.is_closing():
            return
        await self._start_server()
        deadline = time.monotonic() + self.config.accept_timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "Timed out waiting for controller socket client "
                    f"on {self.config.socket_bind_host}:{self.port}."
                )
            reader, writer = await asyncio.wait_for(
                self._accepted_clients.get(),
                timeout=remaining,
            )
            if writer.is_closing():
                continue
            await self._replace_writer(reader, writer)
            return

    async def _replace_writer(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        old_writer = self._writer
        self._reader = reader
        self._writer = writer
        if old_writer is not None and old_writer is not writer:
            old_writer.close()
            with contextlib.suppress(ConnectionError, OSError, RuntimeError):
                await old_writer.wait_closed()

    async def close(self) -> None:
        """Close the TCP socket if it is open."""
        writer = self._writer
        server = self._server
        self._reader = None
        self._writer = None
        self._server = None
        if writer is not None:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError, RuntimeError):
                await writer.wait_closed()
        if server is not None:
            server.close()
            await server.wait_closed()

    def set_target_pos_type(self, pos_type: int) -> None:
        self.target_pos_type = pos_type
        if pos_type == 0:
            self.clear_target_position()

    def set_target_apos(self, position: JsonDict) -> None:
        self.target_apos = copy.deepcopy(position)
        self.target_cpos = None

    def set_target_cpos(self, position: JsonDict) -> None:
        self.target_cpos = copy.deepcopy(position)
        self.target_apos = None

    def clear_target_position(self) -> None:
        self.target_cpos = None
        self.target_apos = None

    async def send_target_move(self, command: int) -> None:
        """Send the cached target to the auto socket project."""
        if command != self.linear_command:
            raise NotImplementedError(
                "Auto socket mode currently supports only linear CPOS moves."
            )
        if self.target_cpos is None:
            raise RuntimeError("Auto socket mode has no cached CPOS target.")
        await self.send_cpos(self.target_cpos)

    async def send_cpos(self, cpos: JsonDict) -> str:
        """Format and send one CPOS target frame."""
        frame = format_auto_socket_frame(cpos, self.config)
        await self.send_frame(frame)
        return frame

    async def send_frame(self, frame: str) -> None:
        """Write a raw frame, reconnecting once if the socket was stale."""
        try:
            await self._send_frame_once(frame)
        except (ConnectionError, OSError, RuntimeError):
            await self.close()
            await self.connect()
            await self._send_frame_once(frame)

    async def _send_frame_once(self, frame: str) -> None:
        await self.connect()
        if self._writer is None:
            raise RuntimeError("Auto socket writer is not connected.")
        payload = frame + ("\n" if self.config.append_newline else "")
        self._writer.write(payload.encode("utf-8"))
        await asyncio.wait_for(
            self._writer.drain(),
            timeout=self.config.write_timeout_s,
        )


class AutoSocketMode:
    """Async context manager returned by CodroidAPI.auto_socket_mode()."""

    def __init__(
        self,
        api: Any,
        config: AutoSocketConfig,
        project_info: Optional[AutoSocketProjectInfo] = None,
    ) -> None:
        self.api = api
        self.config = config
        self.project_info = project_info
        self.backend: Optional[AutoSocketBackend] = None
        self._previous_backend: Optional[AutoSocketBackend] = None

    async def __aenter__(self) -> AutoSocketBackend:
        backend = AutoSocketBackend(
            self.config,
            default_host=self.api.config.host,
            linear_command=self.api.config.commands.move_target_linear,
        )
        try:
            if self.config.connect_on_enter:
                await backend.start_listening()

            info = await self._resolve_project_info()
            if self.config.start_project:
                if info is None:
                    raise ValueError(
                        "start_project requires install_project, project_info, "
                        "or project_id."
                    )
                await self.api.start_auto_socket_project(info, self.config)

            backend.project_info = info
            if self.config.connect_on_enter:
                await backend.connect()
        except BaseException:
            await backend.close()
            raise

        self._previous_backend = self.api._auto_socket_backend
        self.api._auto_socket_backend = backend
        self.backend = backend
        return backend

    async def _resolve_project_info(self) -> Optional[AutoSocketProjectInfo]:
        info = self.project_info
        if self.config.install_project:
            info = await self.api.install_auto_socket_project(self.config)
        elif info is None and self.config.project_id:
            info = AutoSocketProjectInfo(
                project_id=self.config.project_id,
                label=self.config.project_label,
                task_id=self.config.task_id or "",
                run_label_id=self.config.run_label_id or "",
                socket_host=self.config.project_socket_host
                or self.config.socket_host
                or self.api.config.host,
                socket_port=self.config.project_socket_port,
            )
        return info

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.api._auto_socket_backend is self.backend:
            self.api._auto_socket_backend = self._previous_backend
        try:
            if self.config.stop_project_on_exit:
                await self.api.stop_auto_socket_project()
        finally:
            if self.backend is not None:
                await self.backend.close()


def format_auto_socket_frame(cpos: JsonDict, config: AutoSocketConfig) -> str:
    """Return the controller socket frame for one Cartesian target."""
    fields = []
    for key in ("x", "y", "z", "a", "b", "c"):
        if key not in cpos:
            raise ValueError(f"CPOS target is missing required field {key!r}.")
        fields.append(cpos[key])
    values = [format_auto_socket_number(value) for value in fields]
    return (
        config.frame_start
        + config.frame_separator.join(values)
        + config.frame_end
    )


def format_auto_socket_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Auto socket frame values must be numeric.") from exc
    if not math.isfinite(number):
        raise ValueError("Auto socket frame values must be finite numbers.")
    return format(number, ".12g")


def build_auto_socket_project(
    config: AutoSocketConfig,
    *,
    controller_host: str,
    id_factory: Optional[NodeIdFactory] = None,
    mark: Optional[int] = None,
) -> AutoSocketProjectInfo:
    """Build a project document that reads socket frames and performs MovL."""
    new_id = id_factory or _default_node_id
    project_id = config.project_id or new_id("pj")
    task_id = config.task_id or new_id("tk")
    widgets_id = new_id("pr")
    points_id = new_id("po")
    target_point_id = new_id("pt")
    start_socket_id = config.run_label_id or new_id("sc")
    loop_id = new_id("wh")
    run_label_id = start_socket_id
    loop_label_id = new_id("la")
    read_id = new_id("sr")
    pre_read_reconnect_if_id = new_id("if")
    post_read_reconnect_if_id = new_id("if")
    transform_id = new_id("tc")
    move_if_id = new_id("if")

    socket_host = config.project_socket_host or controller_host
    socket_port = config.project_socket_port

    pre_read_reconnect_children = [
        _socket_close_node(
            new_id("sc"),
            ["cmdSock"],
            [loop_id, pre_read_reconnect_if_id],
        ),
        _wait_node(new_id("wt"), 500, [loop_id, pre_read_reconnect_if_id]),
        _socket_create_node(
            new_id("sc"),
            socket_host,
            socket_port,
            [loop_id, pre_read_reconnect_if_id],
        ),
        _goto_node(new_id("gt"), loop_label_id, [loop_id, pre_read_reconnect_if_id]),
    ]
    post_read_reconnect_children = [
        _socket_close_node(
            new_id("sc"),
            ["cmdSock"],
            [loop_id, post_read_reconnect_if_id],
        ),
        _wait_node(new_id("wt"), 500, [loop_id, post_read_reconnect_if_id]),
        _socket_create_node(
            new_id("sc"),
            socket_host,
            socket_port,
            [loop_id, post_read_reconnect_if_id],
        ),
        _goto_node(new_id("gt"), loop_label_id, [loop_id, post_read_reconnect_if_id]),
    ]

    move_children = [
        _movl_node(
            new_id("ml"),
            target_point_id,
            config,
            [loop_id, move_if_id],
        )
    ]

    loop_children = [
        _label_node(loop_label_id, "LOOPSTART", [loop_id]),
        _if_node(
            pre_read_reconnect_if_id,
            _expression(["retCode", "value"], "!=", "0"),
            [loop_id],
            pre_read_reconnect_children,
        ),
        _socket_read_str_node(read_id, config, [loop_id]),
        _if_node(
            post_read_reconnect_if_id,
            _expression(["retCode", "value"], "!=", "0"),
            [loop_id],
            post_read_reconnect_children,
        ),
        _tran_str_to_cpos_node(transform_id, target_point_id, config, [loop_id]),
        _if_node(
            move_if_id,
            _expression(["retCode", "value"], "==", "0"),
            [loop_id],
            move_children,
        ),
    ]

    widgets = {
        "type": "widgets",
        "id": widgets_id,
        "label": "Widgets",
        "uuid": 1,
        "hideoperate": True,
        "treeconfig": _widgets_tree_config(),
        "style": {"zoom": 1, "color": "var(--ccf)"},
        "showchildren": True,
        "doing": {
            "restorepc": {"id": "", "parents": []},
            "current": {"id": "", "parents": []},
            "play": {},
        },
        "parents": [project_id, task_id],
        "children": [
            _socket_create_node(start_socket_id, socket_host, socket_port, []),
            _while_node(loop_id, [], loop_children),
        ],
    }
    points = {
        "type": "points",
        "id": points_id,
        "label": "Points",
        "uuid": 1,
        "hideoperate": True,
        "treeconfig": _points_tree_config(),
        "style": {"zoom": 1, "color": "var(--ccf)"},
        "doing": {"current": {"id": "", "parents": []}, "play": {}},
        "parents": [project_id, task_id],
        "children": [_target_point(target_point_id)],
    }
    project = {
        "children": [
            {
                "type": "task",
                "id": task_id,
                "label": "main",
                "uuid": 1,
                "typeedit": "widgets",
                "typetask": "main",
                "parents": [project_id],
                "children": [widgets, points],
                "show": True,
            }
        ],
        "ver": "1.6.3c",
        "uuid": 1,
        "uuon": 0,
        "type": "project",
        "id": project_id,
        "label": config.project_label,
        "hideoperate": True,
        "mark": mark or int(time.time() * 1000),
        "customconfig": {
            "merge": ["points"],
            "page": "default",
            "robotjoint": 6,
            "robotjointex": 0,
            "robot3d": "axisModel",
            "webspecial": "",
            "webtype": "default",
        },
        "doing": {"tasks": {"type": "widgets", "main": "", "current": "", "play": []}, "vars": ""},
        "simulate": {
            "teachLockArray": {"value": [True] * 6, "key": ["x", "y", "z", "a", "b", "c"]},
            "teachSensitive": 100,
            "speed": 100,
            "features": [],
            "currentFeature": {},
        },
        "vars": {"project": _project_vars()},
        "setting": {},
        "points": True,
        "parents": [],
        "basedata": None,
        "delete": 0,
        "projectcfg": None,
    }
    return AutoSocketProjectInfo(
        project_id=project_id,
        label=config.project_label,
        task_id=task_id,
        run_label_id=run_label_id,
        socket_host=socket_host,
        socket_port=socket_port,
        project=project,
    )


def _default_node_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:14]}"


def _project_vars() -> list[JsonDict]:
    return [
        {
            "key": "cmdSock",
            "type": "Socket",
            "value": {"Socket": {"value": "cmdSock"}},
            "saveflag": False,
        },
        {"key": "retCode", "type": "INT", "value": {"INT": {"value": 0}}, "saveflag": False},
        {
            "key": "cmdText",
            "type": "STRING",
            "value": {"STRING": {"value": ""}},
            "saveflag": False,
        },
    ]


def _empty_cpos() -> JsonDict:
    value = {
        "x": 0,
        "y": 0,
        "z": 0,
        "a": 0,
        "b": 0,
        "c": 0,
        "e": 0,
        "poscfg": {
            "mode": 0,
            "cf1": -1,
            "cf2": -1,
            "cf3": -1,
            "cf4": -1,
            "cf5": -1,
            "cf6": -1,
            "cf7": -1,
        },
    }
    for idx in range(1, 11):
        value[f"exjntpos{idx}"] = 0
    return value


def _target_point(node_id: str) -> JsonDict:
    return {
        "type": "point",
        "icon": "point",
        "id": node_id,
        "label": "AutoTarget",
        "status": 0,
        "datatype": "value",
        "data": {
            "var": "",
            "path": "/auto/socket",
            "value": {
                "posstat": 2,
                "postype": "cpos",
                "posvar": {"datavar": {"default": "DEFAULT", "type": "CPOS", "value": "DEFAULT"}},
                "posvalue": {"cpos": _empty_cpos()},
            },
        },
        "style": {
            "wa": "400px",
            "ha": "380px",
            "color": "var(--ccf)",
            "icolor": "#ffffff",
            "backgroundColor": "#7d94b5",
        },
        "ex": {"ps": ""},
        "path": "/auto/socket",
        "parents": [],
        "children": [],
        "candrop": True,
        "showchildren": True,
        "attrshow": True,
    }


def _socket_create_node(node_id: str, host: str, port: int, parents: list[str]) -> JsonDict:
    return _widget_node(
        node_id,
        "socketcreate",
        "SocketCreate",
        {
            "mscname": {"datavar": {"type": "Socket", "value": "cmdSock"}},
            "mipaddress": host,
            "mport": port,
            "operateval": {"datavar": {"type": "INT", "value": "retCode"}},
        },
        parents,
        background="#e8774b",
    )


def _socket_read_str_node(
    node_id: str,
    config: AutoSocketConfig,
    parents: list[str],
) -> JsonDict:
    return _widget_node(
        node_id,
        "socketreadstr",
        "SocketReadStr",
        {
            "mscname": {"datavar": {"type": "Socket", "value": "cmdSock"}},
            "mdataval": {"datavar": {"type": "STRING", "value": "cmdText"}},
            "mtesttime": config.read_timeout_ms,
            "operateval": {"datavar": {"type": "INT", "value": "retCode"}},
            "strstring": config.frame_start,
            "endstring": config.frame_end,
        },
        parents,
        background="#e8774b",
    )


def _socket_close_node(node_id: str, socket_var: list[str], parents: list[str]) -> JsonDict:
    return _widget_node(
        node_id,
        "socketclose",
        "SocketClose",
        {
            "mscname": {"datavar": {"type": "Socket", "value": socket_var[0]}},
            "operateval": {"datavar": {"type": "INT", "value": ""}},
        },
        parents,
        background="#e8774b",
    )


def _movl_node(
    node_id: str,
    point_id: str,
    config: AutoSocketConfig,
    parents: list[str],
) -> JsonDict:
    return _widget_node(
        node_id,
        "movl",
        "MovL",
        {
            "pointa": {"pointvar": {"type": ["apos", "cpos"], "value": point_id}},
            "mspeed": {
                "datavar": {
                    "type": "SPEED",
                    "value": config.move_speed,
                    "valuekeep": 1,
                }
            },
            "mblendtype": config.move_blend_type,
            "mblendvalue": {"datavar": {"type": "ZONE", "value": config.move_zone}},
            "macc": {
                "datavar": {
                    "type": "ACC",
                    "value": config.move_acc,
                    "valuekeep": 1,
                }
            },
        },
        parents,
        qt="b",
        background="#cc66cc",
        flag={"showplay": 1},
    )


def _tran_str_to_cpos_node(
    node_id: str,
    point_id: str,
    config: AutoSocketConfig,
    parents: list[str],
) -> JsonDict:
    # SocketReadStr consumes the frame delimiters; TranStrToCpos receives the
    # payload between them.
    return _widget_node(
        node_id,
        "transtrtocpos",
        "TranStrToCpos",
        {
            "sortby": 1,
            "currentstr": {"datavar": {"type": "STRING", "value": "cmdText"}},
            "separate": config.frame_separator,
            "pointa": {
                "langs": "memoryvar",
                "pointvar": {"type": ["cpos"], "value": point_id},
            },
            "operateval": {"datavar": {"type": "INT", "value": "retCode"}},
            "strstring": "",
            "endstring": "",
            "angleunit": "deg",
            "lengthunit": "mm",
        },
        parents,
        qt="b",
        background="#7acc89",
    )


def _if_node(
    node_id: str,
    operatordata: list[JsonDict],
    parents: list[str],
    children: list[JsonDict],
) -> JsonDict:
    node = _widget_node(
        node_id,
        "if",
        "If",
        {"operatordata": operatordata},
        parents,
        qt="d",
        background="#6bd6c4",
        children=children,
    )
    return node


def _while_node(node_id: str, parents: list[str], children: list[JsonDict]) -> JsonDict:
    return _widget_node(
        node_id,
        "while",
        "While",
        {
            "operatordata": [
                {
                    "type": "data",
                    "operator": "",
                    "fun": _default_fun(),
                    "data": _while_true_data(),
                }
            ]
        },
        parents,
        qt="d",
        background="#6bd6c4",
        children=children,
    )


def _while_true_data() -> JsonDict:
    return {
        "datatype": "value",
        "data": {
            "point": [],
            "var": [],
            "path": "",
            "value": {"value": "true"},
        },
    }


def _label_node(node_id: str, label: str, parents: list[str]) -> JsonDict:
    return _widget_node(
        node_id,
        "label",
        "Label",
        {"labelname": label},
        parents,
        qt="c",
        background="#6bd6c4",
    )


def _goto_node(node_id: str, target_id: str, parents: list[str]) -> JsonDict:
    return _widget_node(
        node_id,
        "goto",
        "GoTo",
        {"hit": {"type": "node", "id": target_id}},
        parents,
        qt="c",
        background="#6bd6c4",
    )


def _wait_node(node_id: str, ms: int, parents: list[str]) -> JsonDict:
    return _widget_node(
        node_id,
        "wait",
        "Wait",
        {"time": ms},
        parents,
        background="#8470e5",
    )


def _widget_node(
    node_id: str,
    type_name: str,
    label: str,
    value: JsonDict,
    parents: list[str],
    *,
    qt: str = "a",
    background: str,
    children: Optional[list[JsonDict]] = None,
    flag: Optional[JsonDict] = None,
) -> JsonDict:
    return {
        "type": type_name,
        "icon": type_name,
        "qt": qt,
        "id": node_id,
        "index": 0,
        "label": label,
        "datatype": "value",
        "data": {"var": "", "path": "", "value": value},
        "style": {
            "wa": "450px" if qt == "d" else "400px",
            "ha": "480px" if qt == "d" else "360px",
            "color": "var(--ccf)",
            "icolor": "#ffffff",
            "backgroundColor": background,
        },
        "ex": {"ps": ""},
        "parents": parents,
        "children": children or [],
        "flag": flag or {},
        "showchildren": True,
        "attrshow": True,
        "isexplain": False,
    }


def _expression(left_var: list[str], operator: str, value: str) -> list[JsonDict]:
    return [_var_operand(left_var), _operator(operator), _value_operand(value)]


def _var_operand(var_path: list[str]) -> JsonDict:
    return {
        "type": "data",
        "operator": "",
        "fun": _default_fun(),
        "data": {
            "datatype": "var",
            "data": {
                "point": [],
                "var": var_path,
                "path": "",
                "value": {"value": "true"},
            },
        },
    }


def _value_operand(value: str) -> JsonDict:
    return {
        "type": "data",
        "operator": "",
        "data": {
            "datatype": "value",
            "data": {
                "point": "",
                "var": "",
                "path": "",
                "value": {"value": value},
            },
        },
        "fun": _default_fun(),
    }


def _operator(operator: str) -> JsonDict:
    return {
        "type": "operator",
        "operator": operator,
        "data": {
            "datatype": "value",
            "data": {"point": "", "var": "", "path": "", "value": {"value": ""}},
        },
        "fun": _default_fun(),
    }


def _default_fun() -> JsonDict:
    zero_param = {
        "datatype": "value",
        "data": {
            "point": [],
            "var": [],
            "path": "",
            "value": {"value": 0},
        },
    }
    return {
        "funtype": ["math", "sin"],
        "funparams": [copy.deepcopy(zero_param) for _ in range(3)],
    }


def _widgets_tree_config() -> JsonDict:
    return {
        "type": "staggersquare",
        "quickattrshow": 1,
        "attrshow": 1,
        "sortshow": 1,
        "concatshow": 1,
        "styleto": 1,
        "w": "88px",
        "h": "78px",
        "wt": "228px",
        "ht": "48px",
        "hf": "28px",
        "wa": "465px",
        "ha": "380px",
    }


def _points_tree_config() -> JsonDict:
    return {
        "type": "staggercircle",
        "quickattrshow": 1,
        "attrshow": 1,
        "sortshow": 0,
        "concatshow": 1,
        "styleto": 1,
        "w": "98px",
        "h": "98px",
        "wt": "158px",
        "ht": "32px",
        "hf": "48px",
        "wa": "228px",
        "ha": "228px",
        "coordshow": 1,
    }
