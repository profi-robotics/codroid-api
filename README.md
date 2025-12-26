# Codroid API

Python client for the Codroid robot websocket protocol based on the captured `basics.har` traffic.

## Install (uv)

```bash
uv sync
```

## Usage

```python
import asyncio

from codroid_api import CodroidAPI, CodroidConfig


async def main() -> None:
    config = CodroidConfig(host="192.168.101.100")
    async with CodroidAPI(config) as api:
        await api.ws_login()
        await api.robot_login()
        await api.read_config()
        await api.read_system_data()
        await api.read_global_data()
        await api.get_io_info()
        await api.set_language("EN")
        await api.run_project()
        await api.stop_project()

        # Listen for a single incoming message
        message = await api.recv(timeout=5)
        print(message)


if __name__ == "__main__":
    asyncio.run(main())
```

The same flow is available as a runnable script:

```bash
uv run python examples/basic_usage.py
```

## Inspect the capture

List the unique actions captured in the HAR:

```bash
uv run python examples/inspect_capture.py
```

## Replay the captured HAR

Use the capture loader to replay the websocket sends from `basics.har` with overrides for user fields:

```bash
uv run python examples/replay_capture.py
```

## Notes

- Defaults mirror the values in `basics.har`, but all fields are configurable via `CodroidConfig`.
- Responses arrive asynchronously; use `listen()` or `recv()` to consume messages.
