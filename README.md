# Codroid API

Python client for the Codroid robot websocket protocol based on the captured `basics.har` traffic.

## Install (uv)

```bash
uv sync
```

Install the example apps (Dash) with:

```bash
uv sync --extra examples
```

## Configuration

Create a `.env` file (see `.env.example`) and fill in your robot-specific values.

```bash
cp .env.example .env
```

Required for most flows:

- `CODROID_TOKEN`
- `CODROID_USERNAME`
- `CODROID_USER_PASSWORD` (or `CODROID_USERCODE`)
- `CODROID_DEFAULT_PROJECT`, `CODROID_DEFAULT_TASK`, `CODROID_DEFAULT_LABEL` (for `run_project`)

Defaults in `CodroidSettings` mirror the common factory setup; override them in `.env` for your robot.

## Usage

```python
import asyncio

from codroid_api import CodroidAPI, CodroidSettings


async def main() -> None:
    settings = CodroidSettings()
    user_config = settings.build_user_config()
    robot_config = settings.build_robot_config()

    async with CodroidAPI(user_config) as user_api, CodroidAPI(robot_config) as robot_api:
        await user_api.ws_login_with_password()
        await robot_api.robot_login()
        await robot_api.read_config()
        await robot_api.read_system_data()
        await robot_api.read_global_data()
        await robot_api.get_io_info()
        await robot_api.set_language(user_config.default_language)
        await robot_api.run_project()
        await robot_api.stop_project()

        message = await robot_api.recv(timeout=5)
        print(message)


if __name__ == "__main__":
    asyncio.run(main())
```

The same flow is available as a runnable script:

```bash
uv run python examples/basic_usage.py
```

Point move loop example:

```bash
uv run python examples/point_loop.py
```

## Project and point helpers (HTTP)

The API now includes HTTP helpers to manage project files:

```python
# Create a fresh project with no points.
result = await api.create_project(label="NewProject")
project_id = result["id"]

# Add or update a point (provide the full point dict).
await api.upsert_point(project_id, {"label": "P1", "postype": "cpos", "data": {...}})

# Delete a point by id or label.
await api.delete_point(project_id, point_id="pt123")

# Read and save whole project documents directly.
project_doc = await api.read_project(project_id)
await api.save_project(project_doc)

# Delete a project (uses /robot/project/del?id=...).
await api.delete_project(project_id)
```

## Manual jog and point moves

The point-creation capture uses `jogMode/jogIndex/jogSpeed` for manual jogging and
`targetPosType/targetAPos/targetCPos` + command 106/107 for target moves:

```python
# Joint jog (axis 1, positive direction).
await robot_api.start_joint_jog(axis=1, direction=1)
await robot_api.send_command_heartbeat()
await robot_api.stop_jog()

# TCP jog in tool frame.
await robot_api.start_tcp_jog(axis=1, direction=-1, reference=robot_api.config.jog_references.tool)

# Joint target move (APOS/DAPOS).
apos = CodroidAPI.build_target_apos([j1, j2, j3, j4, j5, j6])
await robot_api.move_to_joint_target_linear(apos)

# Cartesian target move (CPOS/DCPOS) with posture control.
poscfg = CodroidAPI.build_poscfg(mode=4)
cpos = CodroidAPI.build_target_cpos(x, y, z, a, b, c, poscfg=poscfg)
await robot_api.move_to_cartesian_target_optimal(cpos)
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

- Secrets and identifiers are loaded from `.env` via `CodroidSettings`.
- Add new command codes in `codroid_api/commands.py` and use `CodroidAPI.set_robot_command()` / `set_param()` to wire them.
- Responses arrive asynchronously; use `listen()` or `recv()` to consume messages.
- The API is only tested against Codroid Web UI v.1.6.3c (matching the captured HAR).
- Responses arrive asynchronously; use `listen()` or `recv()` to consume messages.
