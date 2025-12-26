import asyncio

from codroid_api import CodroidAPI, CodroidConfig


async def main() -> None:
    config = CodroidConfig(
        host="192.168.101.100",
        port=9000,
        origin="http://192.168.101.100:9098",
    )

    async with CodroidAPI(config) as api:
        await api.ws_login()
        await api.robot_login()
        await api.read_config()
        await api.read_system_data()
        await api.read_global_data()
        await api.get_io_info()
        await api.set_language("EN")
        await api.run_project()

        message = await api.recv(timeout=5)
        print("Received message:", message)

        await api.stop_project()


if __name__ == "__main__":
    asyncio.run(main())
