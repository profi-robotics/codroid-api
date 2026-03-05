from __future__ import annotations

import math
import unittest
from unittest import mock

from codroid_api.client import CodroidAPI
from codroid_api.onrobot import OnRobotAction, OnRobotModel, OnRobotProfile


class OnRobotIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_onrobot_profile_sends_expected_paths(self) -> None:
        api = CodroidAPI()
        profile = OnRobotProfile(
            model=OnRobotModel.FG2_7,
            payload_kg=1.25,
            cog_x_m=0.01,
            cog_y_m=0.02,
            cog_z_m=0.03,
            params={"force_pct": 60, "speed_pct": 40},
        )

        with mock.patch.object(api, "set_params", new=mock.AsyncMock()) as set_params:
            await api.set_onrobot_profile(profile)

        set_params.assert_awaited_once_with(
            [
                {"path": "Robot/Control/toolModel", "value": 1},
                {"path": "Robot/Control/toolPayload", "value": 1.25},
                {
                    "path": "Robot/Control/toolCog",
                    "value": {"x": 0.01, "y": 0.02, "z": 0.03},
                },
                {
                    "path": "Robot/Control/toolParams",
                    "value": {"force_pct": 60, "speed_pct": 40},
                },
            ]
        )

    async def test_set_onrobot_payload_validates_numeric_ranges(self) -> None:
        api = CodroidAPI()

        with self.assertRaisesRegex(ValueError, "payload_kg"):
            await api.set_onrobot_payload(-0.1, 0.0, 0.0, 0.0)

        with self.assertRaisesRegex(ValueError, "finite"):
            await api.set_onrobot_payload(0.1, math.nan, 0.0, 0.0)

    async def test_onrobot_action_wrapper_maps_2fg7(self) -> None:
        api = CodroidAPI()

        with mock.patch.object(api, "set_param", new=mock.AsyncMock()) as set_param:
            await api.onrobot_2fg7_open(width_mm=42.0, speed_pct=30)

        set_param.assert_awaited_once_with(
            "Robot/Control/toolAction",
            {
                "model": 1,
                "action": OnRobotAction.OPEN,
                "args": {"width_mm": 42.0, "speed_pct": 30},
            },
        )

    async def test_onrobot_action_wrapper_maps_vgc10_and_soft(self) -> None:
        api = CodroidAPI()

        with mock.patch.object(api, "set_param", new=mock.AsyncMock()) as set_param:
            await api.onrobot_vgc10_vacuum_on(vacuum_pct=85, channel=2)
            await api.onrobot_soft_gripper_grip(pressure_pct=45, duration_ms=500)

        self.assertEqual(set_param.await_count, 2)
        first_call = set_param.await_args_list[0]
        self.assertEqual(first_call.args[0], "Robot/Control/toolAction")
        self.assertEqual(first_call.args[1]["model"], 2)
        self.assertEqual(first_call.args[1]["action"], OnRobotAction.VACUUM_ON)

        second_call = set_param.await_args_list[1]
        self.assertEqual(second_call.args[0], "Robot/Control/toolAction")
        self.assertEqual(second_call.args[1]["model"], 3)
        self.assertEqual(second_call.args[1]["action"], OnRobotAction.GRIP)

    async def test_invalid_model_and_action_raise_value_error(self) -> None:
        api = CodroidAPI()

        with self.assertRaisesRegex(ValueError, "Unsupported OnRobot model"):
            await api.set_onrobot_model("UNKNOWN")

        with self.assertRaisesRegex(ValueError, "Unsupported action"):
            await api.onrobot_action(OnRobotModel.FG2_7, "vacuum_on")


if __name__ == "__main__":
    unittest.main()
