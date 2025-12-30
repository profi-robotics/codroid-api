import asyncio
import json
import time

from codroid_api import CodroidAPI, CodroidSettings


EVENT_ACTIONS = {
    "RobotStatus",
    "RobotPosture",
    "RobotCoordinate",
    "RobotError",
    "RobotWarning",
    "RobotGhost",
    "ProjectStatus",
}


async def wait_for_online(api: CodroidAPI, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = await api.recv(timeout=3)
        except TimeoutError:
            continue
        print("login message:", message)
        if message.get("action") == "online":
            return True
    return False


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
        await robot_api.read_config()
        await robot_api.read_system_data()
        await robot_api.read_global_data()
        await robot_api.get_io_info()
        await robot_api.set_language(user_config.default_language)

        print("Streaming robot events (ws://...:9000). Press Ctrl-C to stop.")
        async for message in robot_api.listen():
            action = message.get("action")
            if message.get("type") == "publish" and action in EVENT_ACTIONS:
                print(json.dumps(message, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
