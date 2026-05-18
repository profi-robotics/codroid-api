from __future__ import annotations

import types
import unittest
from unittest import mock

from codroid_api.client import CodroidAPI
from codroid_api.robot_session import RobotSession


class _FakeWS:
    def __init__(self) -> None:
        self.closed = False
        self.close_code = None
        self.state = "OPEN"


class _FakeAPI:
    def __init__(self, *, keep_open_on_exit: bool = False) -> None:
        self._ws = _FakeWS()
        self.keep_open_on_exit = keep_open_on_exit
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

    async def stop_command(self) -> None:
        self.calls.append("stop_command")

    async def power_off(self) -> None:
        self.calls.append("power_off")

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


class ControlHandoffTests(unittest.IsolatedAsyncioTestCase):
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
