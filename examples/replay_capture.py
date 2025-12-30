import asyncio

from codroid_api import CodroidAPI, CodroidSettings, load_capture


async def main() -> None:
    capture = load_capture("basics.har")

    settings = CodroidSettings()
    overrides = {
        "wslogin": {
            "data": {
                "username": settings.username,
            }
        },
        "Login": {
            "data": {
                "username": settings.username,
                "name": settings.robot_login_name,
                "password": settings.robot_password,
            }
        },
    }

    config = settings.build_robot_config()
    async with CodroidAPI(config) as api:
        await api.replay_capture(capture.send_payloads(), overrides_by_action=overrides, delay_seconds=0.05)


if __name__ == "__main__":
    asyncio.run(main())
