from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from codroid_api.auto_socket import (
    AutoSocketBackend,
    AutoSocketConfig,
    AutoSocketMode,
    AutoSocketProjectInfo,
    DEFAULT_AUTO_SOCKET_PROJECT_ID,
    DEFAULT_AUTO_SOCKET_RUN_LABEL_ID,
    DEFAULT_AUTO_SOCKET_TASK_ID,
    build_auto_socket_project,
    format_auto_socket_frame,
)
from codroid_api.capture import load_capture
from codroid_api.client import CodroidAPI


class _FakeAutoSocketBackend:
    def __init__(self) -> None:
        self.pos_types: list[int] = []
        self.cpos_targets: list[dict] = []
        self.cleared = 0
        self.commands: list[int] = []

    def set_target_pos_type(self, pos_type: int) -> None:
        self.pos_types.append(pos_type)

    def set_target_cpos(self, position: dict) -> None:
        self.cpos_targets.append(position)

    def clear_target_position(self) -> None:
        self.cleared += 1

    async def send_target_move(self, command: int) -> None:
        self.commands.append(command)


class AutoSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_target_move_still_uses_control_params(self) -> None:
        api = CodroidAPI()
        target = CodroidAPI.build_target_cpos(1, 2, 3, 4, 5, 6)

        with mock.patch.object(api, "set_param", new=mock.AsyncMock()) as set_param:
            await api.set_target_cpos(target)
            await api.move_to_target_linear(reset_after=False)

        self.assertEqual(set_param.await_count, 2)
        self.assertEqual(
            set_param.await_args_list[0].args,
            (api.config.control_paths.target_c_pos, target),
        )
        self.assertEqual(
            set_param.await_args_list[1].args,
            (
                api.config.control_paths.command,
                api.config.commands.move_target_linear,
            ),
        )

    async def test_auto_socket_backend_intercepts_linear_move(self) -> None:
        api = CodroidAPI()
        backend = _FakeAutoSocketBackend()
        api._auto_socket_backend = backend
        target = CodroidAPI.build_target_cpos(1, 2, 3, 4, 5, 6)

        with mock.patch.object(api, "set_param", new=mock.AsyncMock()) as set_param:
            await api.set_target_pos_type(api.config.target_pos_types.cpos)
            await api.set_target_cpos(target)
            await api.move_to_target_linear(reset_after=False)
            await api.send_command_heartbeat()
            await api.stop_command()

        set_param.assert_not_awaited()
        self.assertEqual(backend.pos_types, [api.config.target_pos_types.cpos])
        self.assertEqual(backend.cpos_targets, [target])
        self.assertEqual(backend.commands, [api.config.commands.move_target_linear])

    async def test_auto_socket_backend_rejects_optimal_move(self) -> None:
        api = CodroidAPI()
        config = AutoSocketConfig(connect_on_enter=False)
        backend = AutoSocketBackend(
            config,
            default_host=api.config.host,
            linear_command=api.config.commands.move_target_linear,
        )
        api._auto_socket_backend = backend
        await api.set_target_cpos(CodroidAPI.build_target_cpos(1, 2, 3, 4, 5, 6))

        with self.assertRaisesRegex(NotImplementedError, "linear CPOS"):
            await api.move_to_target_optimal(reset_after=False)

    async def test_auto_socket_mode_context_restores_previous_backend(self) -> None:
        api = CodroidAPI()
        previous = _FakeAutoSocketBackend()
        api._auto_socket_backend = previous

        async with api.auto_socket_mode(
            AutoSocketConfig(connect_on_enter=False)
        ) as backend:
            self.assertIs(api._auto_socket_backend, backend)

        self.assertIs(api._auto_socket_backend, previous)

    async def test_auto_socket_mode_listens_before_starting_project(self) -> None:
        events: list[str] = []

        class FakeBackend:
            project_info = None

            def __init__(self, *args, **kwargs) -> None:
                events.append("init")

            async def start_listening(self) -> None:
                events.append("listen")

            async def connect(self) -> None:
                events.append("connect")

            async def close(self) -> None:
                events.append("close")

        class FakeAPI:
            def __init__(self) -> None:
                self.config = SimpleNamespace(
                    host="192.168.101.100",
                    commands=SimpleNamespace(move_target_linear=106),
                )
                self._auto_socket_backend = None

            async def start_auto_socket_project(self, info, config) -> None:
                events.append("start_project")

            async def stop_auto_socket_project(self) -> None:
                events.append("stop_project")

        project_info = AutoSocketProjectInfo(
            project_id="pj_auto",
            label="AutoSocket",
            task_id="tk_auto",
            run_label_id="sc_auto",
            socket_host="192.168.101.110",
            socket_port=8080,
        )

        with mock.patch("codroid_api.auto_socket.AutoSocketBackend", FakeBackend):
            async with AutoSocketMode(
                FakeAPI(),
                AutoSocketConfig(start_project=True, stop_project_on_exit=True),
                project_info,
            ):
                events.append("active")

        self.assertEqual(
            events,
            [
                "init",
                "listen",
                "start_project",
                "connect",
                "active",
                "stop_project",
                "close",
            ],
        )

    async def test_backend_formats_and_writes_frame(self) -> None:
        api = CodroidAPI()
        backend = AutoSocketBackend(
            AutoSocketConfig(connect_on_enter=False),
            default_host=api.config.host,
        )
        cpos = CodroidAPI.build_target_cpos(1.25, 2, 3, 4, 5, 6)

        with mock.patch.object(
            backend,
            "send_frame",
            new=mock.AsyncMock(),
        ) as send_frame:
            frame = await backend.send_cpos(cpos)

        self.assertEqual(frame, "[1.25,2,3,4,5,6]")
        send_frame.assert_awaited_once_with("[1.25,2,3,4,5,6]")

    async def test_backend_server_accepts_controller_client(self) -> None:
        port = _free_tcp_port()
        backend = AutoSocketBackend(
            AutoSocketConfig(
                socket_role="server",
                socket_bind_host="127.0.0.1",
                socket_port=port,
                accept_timeout_s=2.0,
            ),
            default_host="127.0.0.1",
        )

        connect_task = asyncio.create_task(backend.connect())
        await asyncio.sleep(0.05)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await connect_task
            await backend.send_frame("[1,2,3,4,5,6]")
            data = await asyncio.wait_for(reader.readexactly(13), timeout=1.0)
        finally:
            writer.close()
            await writer.wait_closed()
            await backend.close()

        self.assertEqual(data, b"[1,2,3,4,5,6]")


class AutoSocketProjectTests(unittest.TestCase):
    def test_project_generation_contains_string_reader_transform_and_movl(self) -> None:
        counter = 0

        def next_id(prefix: str) -> str:
            nonlocal counter
            counter += 1
            return f"{prefix}{counter:02d}"

        info = build_auto_socket_project(
            AutoSocketConfig(
                project_id="pj_auto",
                task_id=None,
                run_label_id=None,
                project_label="AutoSocket",
            ),
            controller_host="192.168.101.100",
            id_factory=next_id,
            mark=123,
        )
        project = info.project
        self.assertIsNotNone(project)
        self.assertEqual(info.project_id, "pj_auto")
        self.assertEqual(info.socket_host, "192.168.101.100")
        self.assertEqual(info.socket_port, 8080)
        self.assertEqual(info.run_label_id, "sc05")

        nodes = list(_walk_nodes(project))
        by_type = {}
        for node in nodes:
            by_type.setdefault(node.get("type"), []).append(node)

        self.assertEqual(project["vars"]["project"][0]["type"], "Socket")
        self.assertEqual(project["vars"]["project"][1]["type"], "INT")
        self.assertEqual(project["vars"]["project"][2]["type"], "STRING")
        top_level_widget_types = [
            child["type"]
            for child in project["children"][0]["children"][0]["children"]
        ]
        self.assertEqual(top_level_widget_types, ["socketcreate", "while"])
        self.assertEqual(by_type["socketcreate"][0]["data"]["value"]["mport"], 8080)
        loop_child_types = [
            child["type"] for child in by_type["while"][0]["children"]
        ]
        self.assertEqual(
            loop_child_types,
            ["label", "if", "socketreadstr", "if", "transtrtocpos", "if"],
        )
        first_reconnect_children = by_type["while"][0]["children"][1]["children"]
        self.assertEqual(
            [child["type"] for child in first_reconnect_children],
            ["socketclose", "wait", "socketcreate", "goto"],
        )
        read_str = by_type["socketreadstr"][0]["data"]["value"]
        self.assertEqual(read_str["mdataval"]["datavar"]["type"], "STRING")
        self.assertEqual(read_str["mdataval"]["datavar"]["value"], "cmdText")
        self.assertEqual(read_str["strstring"], "[")
        self.assertEqual(read_str["endstring"], "]")
        transform = by_type["transtrtocpos"][0]["data"]["value"]
        self.assertEqual(transform["currentstr"]["datavar"]["value"], "cmdText")
        self.assertEqual(transform["strstring"], "")
        self.assertEqual(transform["endstring"], "")
        self.assertEqual(transform["separate"], ",")
        self.assertEqual(transform["angleunit"], "deg")
        self.assertEqual(transform["lengthunit"], "mm")
        self.assertEqual(
            transform["pointa"]["pointvar"]["value"],
            "pt04",
        )
        self.assertEqual(by_type["movl"][0]["data"]["value"]["mblendtype"], "FINE")
        self.assertEqual(by_type["while"][0]["parents"], [])
        while_true = (
            by_type["while"][0]["data"]["value"]["operatordata"][0]
            ["data"]["data"]
        )
        self.assertEqual(while_true["point"], [])
        self.assertEqual(while_true["var"], [])
        self.assertEqual(while_true["value"]["value"], "true")
        generated_widgets = [
            node for node in nodes
            if node.get("type") in {
                "socketcreate",
                "socketreadstr",
                "transtrtocpos",
                "socketclose",
                "wait",
                "goto",
                "label",
                "while",
                "if",
                "movl",
            }
        ]
        self.assertTrue(generated_widgets)
        self.assertTrue(
            all(node.get("isexplain") is False for node in generated_widgets)
        )

    def test_project_generation_honors_custom_run_label(self) -> None:
        info = build_auto_socket_project(
            AutoSocketConfig(
                project_id="pj_auto",
                task_id="tk_auto",
                run_label_id="custom_start",
            ),
            controller_host="192.168.101.100",
            id_factory=lambda prefix: f"{prefix}_id",
            mark=123,
        )

        self.assertEqual(info.run_label_id, "custom_start")
        self.assertEqual(
            info.project["children"][0]["children"][0]["children"][0]["id"],
            "custom_start",
        )

    def test_default_project_generation_uses_single_managed_project(self) -> None:
        info = build_auto_socket_project(
            AutoSocketConfig(),
            controller_host="192.168.101.100",
            id_factory=lambda prefix: f"{prefix}_id",
            mark=123,
        )

        self.assertEqual(info.project_id, DEFAULT_AUTO_SOCKET_PROJECT_ID)
        self.assertEqual(info.task_id, DEFAULT_AUTO_SOCKET_TASK_ID)
        self.assertEqual(info.run_label_id, DEFAULT_AUTO_SOCKET_RUN_LABEL_ID)
        self.assertEqual(info.project["id"], DEFAULT_AUTO_SOCKET_PROJECT_ID)

    def test_api_auto_socket_project_uses_local_server_host(self) -> None:
        api = CodroidAPI()
        with mock.patch.object(
            api,
            "_local_source_ip_for",
            return_value="192.168.101.110",
        ):
            info = api.build_auto_socket_project(
                AutoSocketConfig(project_id="pj_auto")
            )

        self.assertEqual(info.socket_host, "192.168.101.110")

    def test_format_auto_socket_frame_rejects_non_numeric_values(self) -> None:
        with self.assertRaises(ValueError):
            format_auto_socket_frame(
                {"x": "nan", "y": 2, "z": 3, "a": 4, "b": 5, "c": 6},
                AutoSocketConfig(),
            )


class CaptureHttpTests(unittest.TestCase):
    def test_project_edit_payloads_are_extracted_from_har(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "startedDateTimeUnix": 123.0,
                        "request": {
                            "method": "POST",
                            "url": "http://ctrl/robot/project/edit",
                            "postData": {
                                "text": json.dumps(
                                    {"id": "pj1", "label": "AutoSocket"}
                                )
                            },
                        },
                        "response": {"status": 200, "content": {"text": "{}"}},
                    },
                    {
                        "startedDateTimeUnix": 124.0,
                        "request": {
                            "method": "POST",
                            "url": "http://ctrl/robot/project/bak",
                            "postData": {
                                "text": json.dumps(
                                    {"id": "pj2", "label": "AutoSocketBak"}
                                )
                            },
                        },
                        "response": {"status": 200, "content": {"text": "{}"}},
                    },
                    {
                        "request": {"method": "GET", "url": "http://ctrl/"},
                        "response": {"status": 200},
                        "_webSocketMessages": [
                            {
                                "type": "send",
                                "time": 1,
                                "opcode": 1,
                                "data": json.dumps(
                                    {"type": "Robot", "action": "Login"}
                                ),
                            }
                        ],
                    },
                ]
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "capture.har"
            path.write_text(json.dumps(har))
            capture = load_capture(path)

        self.assertEqual(capture.actions(), ["Login"])
        self.assertEqual(
            capture.project_edit_payloads(),
            [
                {"id": "pj1", "label": "AutoSocket"},
                {"id": "pj2", "label": "AutoSocketBak"},
            ],
        )


def _walk_nodes(value):
    if isinstance(value, dict):
        if "type" in value:
            yield value
        for child in value.get("children", []) or []:
            yield from _walk_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_nodes(child)


def _free_tcp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


if __name__ == "__main__":
    unittest.main()
