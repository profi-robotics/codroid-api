from __future__ import annotations

import asyncio
import types
import unittest
from unittest import mock

from codroid_api.client import CodroidAPI, CodroidConfig
from codroid_api.robot_session import RobotSession
from codroid_api.settings import CodroidSettings


class _FakeWS:
    def __init__(self) -> None:
        self.closed = False
        self.close_code = None
        self.state = "OPEN"


class _FakeAPI:
    def __init__(
        self,
        *,
        keep_open_on_exit: bool = False,
        recv_messages: list[object] | None = None,
    ) -> None:
        self._ws = _FakeWS()
        self.keep_open_on_exit = keep_open_on_exit
        self.recv_messages = list(recv_messages or [{"action": "online"}])
        self.calls: list[str] = []
        self.config = types.SimpleNamespace(
            username="operator",
            usercode="uc",
            userwsid="wsid",
            ws_user_type="wsuser",
            robot_login_name="web",
            robot_password="",
            robot_ws_type="wsrobot",
        )

    async def __aenter__(self):
        self.calls.append("__aenter__")
        return self

    async def stop_command(self) -> None:
        self.calls.append("stop_command")

    async def power_off(self) -> None:
        self.calls.append("power_off")

    async def robot_login(self) -> None:
        self.calls.append("robot_login")

    async def read_config(self) -> None:
        self.calls.append("read_config")

    async def read_system_data(self) -> None:
        self.calls.append("read_system_data")

    async def read_global_data(self) -> None:
        self.calls.append("read_global_data")

    async def get_io_info(self) -> None:
        self.calls.append("get_io_info")

    async def set_language(self) -> None:
        self.calls.append("set_language")

    async def ws_login_with_password(self) -> None:
        self.calls.append("ws_login_with_password")

    async def ws_login(self) -> None:
        self.calls.append("ws_login")

    async def recv(self, timeout: float = 1.0) -> dict:
        self.calls.append(f"recv:{timeout}")
        if self.recv_messages:
            message = self.recv_messages.pop(0)
            if isinstance(message, BaseException):
                raise message
            return message
        return {"action": "online"}

    async def listen(self):
        if False:
            yield {}

    def build_user_logout_message(self, action: str = "wslogout") -> dict:
        return {"action": action, "type": "user"}

    def build_robot_logout_message(self, action: str = "Logout") -> dict:
        return {"action": action, "type": "robot"}

    async def send_message(self, message: dict) -> None:
        self.calls.append(f"send:{message.get('type')}:{message.get('action')}")

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.calls.append("__aexit__")
        if not self.keep_open_on_exit:
            self._ws.closed = True
            self._ws.close_code = 1000
            self._ws.state = "CLOSED"


class _FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.close_code = None
        self.state = "OPEN"

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(60.0)
        raise StopAsyncIteration

    async def close(self) -> None:
        self.closed = True
        self.close_code = 1000
        self.state = "CLOSED"


class _FailingWebSocket:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise RuntimeError("receiver failed")


class ControlHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_connect_uses_configured_timeouts(self) -> None:
        api = CodroidAPI(
            CodroidConfig(
                host="host",
                port=9098,
                websocket_open_timeout_s=3.0,
                websocket_close_timeout_s=1.0,
                websocket_ping_interval_s=None,
                websocket_ping_timeout_s=12.0,
            )
        )
        fake_ws = _FakeWebSocket()

        with mock.patch(
            "codroid_api.client.websockets.connect",
            new=mock.AsyncMock(return_value=fake_ws),
        ) as connect_mock:
            await api.connect()

        connect_mock.assert_awaited_once_with(
            "ws://host:9098/",
            open_timeout=3.0,
            close_timeout=1.0,
            ping_interval=None,
            ping_timeout=12.0,
            origin="http://codroid-controller.local:9098",
        )
        await api.close()

    async def test_receiver_task_swallows_background_errors(self) -> None:
        api = CodroidAPI()
        api._ws = _FailingWebSocket()

        await api._receiver()

    async def test_ws_login_with_password_persists_runtime_context(self) -> None:
        api = CodroidAPI()
        with mock.patch.object(
            api,
            "http_login",
            new=mock.AsyncMock(return_value={"usercode": "dyn-code"}),
        ), mock.patch.object(api, "send_message", new=mock.AsyncMock()):
            await api.ws_login_with_password(
                username="operator",
                password="x",
                userwsid="ws-123",
            )
        self.assertEqual(api.active_usercode, "dyn-code")
        self.assertEqual(api.active_userwsid, "ws-123")
        self.assertEqual(api.active_user_login_type, "password")
        self.assertEqual(api.config.usercode, "dyn-code")
        self.assertEqual(api.config.userwsid, "ws-123")

    async def test_release_control_happy_path_orders_and_closes(self) -> None:
        session = RobotSession(release_grace_period_s=0.0)
        user_api = _FakeAPI()
        robot_api = _FakeAPI()
        session.user_api = user_api
        session.user_uri = "ws://host:9098/"
        session.robot_api = robot_api
        session.robot_uri = "ws://host:9000/"

        state = await session.release_control("ws://host:9000/", power_off=True, wait_closed_s=0.2)

        self.assertEqual(state["mode"], "released")
        self.assertGreater(state["released_at"], 0.0)
        self.assertIsNone(state["last_error"])
        self.assertIn("stop_command", robot_api.calls)
        self.assertIn("power_off", robot_api.calls)
        self.assertIn("__aexit__", user_api.calls)
        self.assertIn("__aexit__", robot_api.calls)
        self.assertIsNone(session.user_api)
        self.assertIsNone(session.robot_api)

    async def test_release_control_without_power_off_skips_power_command(self) -> None:
        session = RobotSession(release_grace_period_s=0.0)
        session.user_api = _FakeAPI()
        session.user_uri = "ws://host:9098/"
        robot_api = _FakeAPI()
        session.robot_api = robot_api
        session.robot_uri = "ws://host:9000/"

        state = await session.release_control("ws://host:9000/", power_off=False, wait_closed_s=0.2)

        self.assertEqual(state["mode"], "released")
        self.assertIn("stop_command", robot_api.calls)
        self.assertNotIn("power_off", robot_api.calls)

    async def test_release_user_web_session_keeps_robot_connection(self) -> None:
        session = RobotSession(release_grace_period_s=0.0)
        user_api = _FakeAPI()
        robot_api = _FakeAPI()
        session.user_api = user_api
        session.user_uri = "ws://host:9098/"
        session.robot_api = robot_api
        session.robot_uri = "ws://host:9000/"

        state = await session.release_user_web_session(wait_closed_s=0.2)

        self.assertTrue(state["released"])
        self.assertIn("send:user:wslogout", user_api.calls)
        self.assertIn("__aexit__", user_api.calls)
        self.assertIsNone(session.user_api)
        self.assertIs(session.robot_api, robot_api)
        self.assertFalse(RobotSession._api_is_open(user_api))
        self.assertTrue(RobotSession._api_is_open(robot_api))

    async def test_keep_user_web_session_false_releases_after_connect(self) -> None:
        settings = CodroidSettings(
            host="host",
            ws_port=9098,
            robot_port=9000,
            keep_user_web_session=False,
        )
        session = RobotSession(settings, release_grace_period_s=0.0)
        user_api = _FakeAPI()
        robot_api = _FakeAPI()

        with mock.patch(
            "codroid_api.robot_session.CodroidAPI",
            side_effect=[user_api, robot_api],
        ):
            connected = await session.connect("ws://host:9000/")

        self.assertIs(connected, robot_api)
        self.assertIsNone(session.user_api)
        self.assertIs(session.robot_api, robot_api)
        self.assertIn("send:user:wslogout", user_api.calls)
        self.assertIn("__aexit__", user_api.calls)
        self.assertTrue(RobotSession._api_is_open(robot_api))

    async def test_user_login_ignores_non_dict_boot_messages(self) -> None:
        settings = CodroidSettings(
            host="host",
            ws_port=9098,
            robot_port=9000,
            keep_user_web_session=False,
        )
        session = RobotSession(settings, release_grace_period_s=0.0)
        user_api = _FakeAPI(recv_messages=[None, {"action": "online"}])
        robot_api = _FakeAPI()

        with mock.patch(
            "codroid_api.robot_session.CodroidAPI",
            side_effect=[user_api, robot_api],
        ):
            connected = await session.connect("ws://host:9000/")

        self.assertIs(connected, robot_api)
        self.assertIn("recv:1.0", user_api.calls)
        self.assertIs(session.robot_api, robot_api)

    async def test_user_login_retries_user_online_timeout(self) -> None:
        settings = CodroidSettings(
            host="host",
            ws_port=9098,
            robot_port=9000,
            keep_user_web_session=False,
        )
        session = RobotSession(settings, release_grace_period_s=0.0)
        user_api = _FakeAPI(
            recv_messages=[asyncio.TimeoutError(), {"action": "online"}]
        )
        robot_api = _FakeAPI()

        with mock.patch(
            "codroid_api.robot_session.CodroidAPI",
            side_effect=[user_api, robot_api],
        ):
            connected = await session.connect("ws://host:9000/")

        self.assertIs(connected, robot_api)
        self.assertEqual(user_api.calls.count("recv:1.0"), 2)

    async def test_failed_user_connection_closes_partial_api(self) -> None:
        session = RobotSession(release_grace_period_s=0.0)
        user_api = _FakeAPI()

        async def _fail_enter():
            user_api.calls.append("__aenter__")
            raise TimeoutError("timed out during opening handshake")

        user_api.__aenter__ = _fail_enter

        with mock.patch(
            "codroid_api.robot_session.CodroidAPI",
            return_value=user_api,
        ):
            with self.assertRaisesRegex(TimeoutError, "opening handshake"):
                await session.connect("ws://host:9000/")

        self.assertIn("__aexit__", user_api.calls)
        self.assertIsNone(session.user_api)
        self.assertIsNone(session.user_uri)

    async def test_release_control_verification_failure_sets_error(self) -> None:
        session = RobotSession(release_grace_period_s=0.0)
        session.user_api = _FakeAPI(keep_open_on_exit=True)
        session.user_uri = "ws://host:9098/"
        session.robot_api = _FakeAPI(keep_open_on_exit=True)
        session.robot_uri = "ws://host:9000/"

        with self.assertRaisesRegex(RuntimeError, "close verification failed"):
            await session.release_control("ws://host:9000/", power_off=True, wait_closed_s=0.05)

        state = session.control_state()
        self.assertEqual(state["mode"], "acquired")
        self.assertGreater(len(state["last_error"] or ""), 0)

    async def test_acquire_control_reconnects_and_marks_acquired(self) -> None:
        session = RobotSession(release_grace_period_s=0.0)
        session._control_mode = "released"
        session._released_at = 10.0
        fake_api = _FakeAPI()

        async def _fake_connect(_uri: str):
            session._control_mode = "acquired"
            session._released_at = 0.0
            return fake_api

        with mock.patch.object(session, "_connect_on_loop", new=_fake_connect):
            got = await session.acquire_control("ws://host:9000/")

        self.assertIs(got, fake_api)
        self.assertEqual(session.control_state()["mode"], "acquired")

    async def test_acquire_control_force_reconnect_releases_then_connects(self) -> None:
        session = RobotSession(release_grace_period_s=0.0)
        calls: list[str] = []

        async def _fake_release(*, resolved_uri: str, power_off: bool, wait_closed_s: float, mark_released: bool):
            calls.append(f"release:{resolved_uri}:{power_off}:{wait_closed_s}:{mark_released}")
            return session.control_state()

        async def _fake_connect(uri: str):
            calls.append(f"connect:{uri}")
            return _FakeAPI()

        with mock.patch.object(session, "_release_connections_on_loop", new=_fake_release), mock.patch.object(
            session, "_connect_on_loop", new=_fake_connect
        ):
            await session.acquire_control("ws://host:9000/", force_reconnect=True)

        self.assertEqual(
            calls,
            [
                "release:ws://host:9000/:False:2.0:False",
                "connect:ws://host:9000/",
            ],
        )

    async def test_probe_strict_mode_blocks_when_released(self) -> None:
        session = RobotSession()
        session._control_mode = "released"
        with self.assertRaisesRegex(RuntimeError, "acquire control first"):
            await session.probe("ws://host:9000/", require_acquired=True)

    async def test_close_swallows_release_exceptions(self) -> None:
        session = RobotSession()
        with mock.patch.object(
            session,
            "_release_connections_on_loop",
            new=mock.AsyncMock(side_effect=RuntimeError("close boom")),
        ):
            await session.close()

    async def test_run_with_robot_strict_mode_blocks_when_released(self) -> None:
        session = RobotSession()
        session._control_mode = "released"
        async def _noop(_api) -> None:
            return None
        with self.assertRaisesRegex(RuntimeError, "acquire control first"):
            await session.run_with_robot(
                "ws://host:9000/",
                _noop,
                require_acquired=True,
            )


if __name__ == "__main__":
    unittest.main()
