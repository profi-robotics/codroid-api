"""
Send a stop + power-off command to the robot controller.

Use this to verify the CodroidAPI emergency stop path.
"""

from __future__ import annotations

import asyncio

from codroid_api import CodroidAPI, CodroidSettings


async def _prompt(message: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, input, message)


async def main() -> None:
    settings = CodroidSettings()
    robot_config = settings.build_robot_config()

    async with CodroidAPI(robot_config) as api:
        await api.robot_login()
        await _prompt("Press Enter to switch to auto mode... ")
        await api.set_auto_mode()
        print("Auto mode command sent.")

        await _prompt("Press Enter to switch to manual mode... ")
        await api.set_manual_mode()
        print("Manual mode command sent.")

        await _prompt("Press Enter to send stop + power-off... ")
        await api.stop_command()
        await api.power_off()
        print("Stop + power-off command sent.")

        await _prompt("Press Enter to power back on... ")
        await api.power_on()
        print("Power on command sent.")


if __name__ == "__main__":
    asyncio.run(main())
