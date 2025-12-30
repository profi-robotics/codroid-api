import asyncio
import time
from typing import Any, Dict, Optional

from codroid_api import CodroidAPI, CodroidSettings


async def wait_for_online(api: CodroidAPI, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = await api.recv(timeout=3)
        except TimeoutError:
            continue
        if message.get("action") == "online":
            return True
    return False


async def wait_for_robot_posture(
    api: CodroidAPI, timeout: float = 15.0
) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = await api.recv(timeout=3)
        except TimeoutError:
            continue
        if message.get("action") == "RobotPosture":
            return message
    return None


def extract_posture_end(message: Dict[str, Any]) -> Dict[str, Any]:
    data = message.get("data", {})
    if isinstance(data, dict) and "data" in data:
        data = data.get("data", {})
    end = data.get("end")
    if not isinstance(end, dict):
        return {}
    return end


def offset_cpos(base: Dict[str, Any], dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> Dict[str, Any]:
    updated = dict(base)
    updated["x"] = base["x"] + dx
    updated["y"] = base["y"] + dy
    updated["z"] = base["z"] + dz
    return updated


async def login_user(api: CodroidAPI, config) -> None:
    if config.user_password:
        await api.ws_login_with_password()
    else:
        await api.ws_login()


async def main() -> None:
    settings = CodroidSettings()
    user_config = settings.build_user_config()
    robot_config = settings.build_robot_config()

    async with CodroidAPI(user_config) as user_api, CodroidAPI(robot_config) as robot_api:
        await login_user(user_api, user_config)
        if not await wait_for_online(user_api):
            print("online response not received.")
            return

        await robot_api.robot_login()

        posture_message = await wait_for_robot_posture(robot_api)
        if posture_message is None:
            print("Timed out waiting for RobotPosture.")
            return

        end = extract_posture_end(posture_message)
        if not end:
            print("RobotPosture did not include end pose data.")
            return

        poscfg = CodroidAPI.build_poscfg(mode=int(end.get("mode", 0)))
        base_cpos = CodroidAPI.build_target_cpos(
            x=end["x"],
            y=end["y"],
            z=end["z"],
            a=end["a"],
            b=end["b"],
            c=end["c"],
            poscfg=poscfg,
        )

        # Points are tracked locally because point CRUD calls are not captured yet.
        point = {"name": "demo_point", "position": base_cpos}

        move_delay = 0.5
        delta_x = 10.0

        for i in range(10):
            point["position"] = base_cpos
            await robot_api.move_to_cartesian_target_linear(point["position"])
            await asyncio.sleep(move_delay)

            direction = 1 if i % 2 == 0 else -1
            updated_cpos = offset_cpos(base_cpos, dx=direction * delta_x)
            point["position"] = updated_cpos
            await robot_api.move_to_cartesian_target_linear(point["position"])
            await asyncio.sleep(move_delay)


if __name__ == "__main__":
    asyncio.run(main())
