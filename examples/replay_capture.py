import asyncio

from codroid_api import CodroidAPI, CodroidConfig, load_capture


async def main() -> None:
    capture = load_capture("basics.har")

    overrides = {
        "wslogin": {
            "data": {
                "username": "admin",
            }
        },
        "Login": {
            "data": {
                "username": "admin",
                "name": "web",
                "password": "",
            }
        },
    }

    config = CodroidConfig(host="192.168.101.100")
    async with CodroidAPI(config) as api:
        await api.replay_capture(capture.send_payloads(), overrides_by_action=overrides, delay_seconds=0.05)


if __name__ == "__main__":
    asyncio.run(main())
