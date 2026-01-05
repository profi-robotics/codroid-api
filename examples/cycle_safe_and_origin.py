"""
Cycle the robot between the Safe preset and a user-coordinate origin.

Adjust the coordinate ID, hold times, and dwell durations as needed.
Stop with Ctrl+C.
"""

from __future__ import annotations

import asyncio

from codroid_api import CodroidAPI, CodroidSettings


async def main() -> None:
    settings = CodroidSettings()
    robot_config = settings.build_robot_config()

    coordinate_id = 6       # calibrated user coordinate slot
    hold_seconds = 5.0      # heartbeat duration for each move command
    dwell_seconds = 1.0     # pause after each move

    async with CodroidAPI(robot_config) as api:
        await api.robot_login()
        print("Cycling between Safe and coordinate origin. Ctrl+C to stop.")
        while True:
            print("Moving to Safe location...")
            await api.move_safe(hold_seconds=hold_seconds)
            await asyncio.sleep(dwell_seconds)

            print(f"Moving to origin of coordinate ID {coordinate_id}...")
            await api.move_to_coordinate_origin(
                coordinate_id=coordinate_id,
                linear=True,
                hold_seconds=hold_seconds,
                reset_after=True,
            )
            await asyncio.sleep(dwell_seconds)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped.")
