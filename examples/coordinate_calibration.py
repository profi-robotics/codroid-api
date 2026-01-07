"""
Interactive three-point coordinate calibration using CodroidAPI.

Steps:
- Connect to the robot WS channel and track the latest TCP posture.
- Prompt the user to move the robot to three points and press Enter each time.
- Ensure CoordinateId 0 (base) is active before capturing points.
- Send the `Robot/CoordinateCalibration` request tagged to user coordinate ID 6.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Dict, Optional

from codroid_api import CodroidAPI, CodroidSettings


async def _prompt(message: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, input, message)


async def main() -> None:
    settings = CodroidSettings()
    robot_config = settings.build_robot_config()
    coordinate_id = 6

    latest_posture: Dict[str, Any] = {}
    posture_event = asyncio.Event()
    coordinate_event = asyncio.Event()
    current_coordinate_id: Optional[int] = None
    calibration_future: Optional[asyncio.Future[Dict[str, Any]]] = None
    coordinate_frame_event = asyncio.Event()
    latest_coordinate_frame: Dict[str, Any] = {}
    coordinate_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

    async with CodroidAPI(robot_config) as robot_api:
        await robot_api.robot_login()

        async def _listener() -> None:
            nonlocal latest_posture, current_coordinate_id, calibration_future, latest_coordinate_frame
            async for msg in robot_api.listen():
                action = msg.get("action")
                msg_type = msg.get("type")
                if action == "RobotPosture":
                    data = (msg.get("data") or {}).get("data") or {}
                    latest_posture = data.get("end") or latest_posture
                    posture_event.set()
                if action == "RobotCoordinate":
                    data = (msg.get("data") or {}).get("data") or {}
                    latest_coordinate_frame = data.get("user") or latest_coordinate_frame
                    coordinate_frame_event.set()
                    coordinate_queue.put_nowait({"time": msg.get("time"), "frame": latest_coordinate_frame})
                if action == "RobotStatus":
                    status_payload = (msg.get("data") or {}).get("data") or {}
                    status = status_payload.get("data") or status_payload
                    coord_id = status.get("CoordinateId")
                    if coord_id is not None:
                        current_coordinate_id = int(coord_id)
                        coordinate_event.set()
                if msg_type == "Robot" and action == "CoordinateCalibration":
                    if calibration_future and not calibration_future.done():
                        calibration_future.set_result(msg)

        listener_task = asyncio.create_task(_listener())

        try:
            async def _ensure_coordinate_id(
                expected_id: int,
                *,
                require: bool = True,
                timeout_s: float = 5.0,
            ) -> bool:
                deadline = asyncio.get_running_loop().time() + timeout_s
                coordinate_event.clear()
                await robot_api.set_current_coordinate_id(expected_id)
                while asyncio.get_running_loop().time() < deadline:
                    remaining = deadline - asyncio.get_running_loop().time()
                    try:
                        await asyncio.wait_for(
                            coordinate_event.wait(), timeout=min(0.5, remaining)
                        )
                    except asyncio.TimeoutError:
                        continue
                    if current_coordinate_id == expected_id:
                        return True
                    coordinate_event.clear()

                message = (
                    f"Expected CoordinateId {expected_id}, got {current_coordinate_id}."
                )
                if require:
                    print(f"Error: {message} Calibration aborted.")
                else:
                    print(f"Warning: {message} Continuing anyway.")
                return False

            # Require base coordinate (ID 0) for accurate posture capture.
            if not await _ensure_coordinate_id(0, require=True):
                return

            points = []
            for idx in range(1, 4):
                posture_event.clear()
                await _prompt(f"Move robot to point {idx} and press Enter...")
                await asyncio.wait_for(posture_event.wait(), timeout=5.0)
                cpos = CodroidAPI.build_target_cpos(
                    x=float(latest_posture["x"]),
                    y=float(latest_posture["y"]),
                    z=float(latest_posture["z"]),
                    a=float(latest_posture["a"]),
                    b=float(latest_posture["b"]),
                    c=float(latest_posture["c"]),
                )
                points.append(cpos)
                print(f"Captured point {idx}: {latest_posture}")

            # Switch to the target coordinate slot before calibration/persist.
            await _ensure_coordinate_id(coordinate_id, require=False)

            # Send calibration request and wait for response via listener
            calibration_future = asyncio.get_running_loop().create_future()
            request = robot_api.build_message(
                message_type="Robot",
                action="CoordinateCalibration",
                data=[
                    robot_api.attach_coordinate_to_cpos(p, coordinate_id=coordinate_id)
                    for p in points
                ],
            )
            await robot_api.send_message(request)
            response = await asyncio.wait_for(calibration_future, timeout=10.0)
            result = (response.get("data") or {}).get("data", {})
            print(f"Calibration complete for coordinate ID {coordinate_id}:", result)

            # Persist the calibrated frame into the requested coordinate slot.
            if result:
                await robot_api.change_coordinate_parameter(coordinate_id, result)
                print(f"Coordinate parameters updated for ID {coordinate_id}.")

            # First try to consume a RobotCoordinate publish naturally after calibration
            while not coordinate_queue.empty():
                coordinate_queue.get_nowait()
            try:
                coord_msg = await asyncio.wait_for(coordinate_queue.get(), timeout=5.0)
                print(f"RobotCoordinate published (ID {coordinate_id}): {coord_msg['frame']}")
            except asyncio.TimeoutError:
                # Fall back to reasserting CoordinateId 6 to trigger a publish
                coordinate_event.clear()
                coordinate_frame_event.clear()
                await robot_api.set_current_coordinate_id(coordinate_id)
                try:
                    await asyncio.wait_for(coordinate_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                try:
                    coord_msg = await asyncio.wait_for(coordinate_queue.get(), timeout=5.0)
                    print(f"RobotCoordinate published after reselect (ID {coordinate_id}): {coord_msg['frame']}")
                except asyncio.TimeoutError:
                    print("Warning: no RobotCoordinate publish received after calibration.")
        finally:
            listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener_task


if __name__ == "__main__":
    asyncio.run(main())
