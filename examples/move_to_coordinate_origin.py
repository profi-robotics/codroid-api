"""
Example: move to the origin of a calibrated user coordinate frame.

Set the desired coordinate ID (e.g., 6) and choose linear/optimal motion.
"""

from __future__ import annotations

import asyncio

from codroid_api import CodroidAPI, CodroidSettings


async def main() -> None:
    settings = CodroidSettings()
    robot_config = settings.build_robot_config()

    coordinate_id = 6  # change to your calibrated user coordinate slot
    linear_move = True
    hold_seconds = 5.0  # send command heartbeats for this long

    async with CodroidAPI(robot_config) as api:
        await api.robot_login()
        print(f"Moving to origin of coordinate ID {coordinate_id} "
              f"using {'linear' if linear_move else 'optimal'} motion...")
        await api.move_to_coordinate_origin(
            coordinate_id=coordinate_id,
            linear=linear_move,
            reset_after=True,
            hold_seconds=hold_seconds,
        )
        print("Move complete.")


if __name__ == "__main__":
    asyncio.run(main())
