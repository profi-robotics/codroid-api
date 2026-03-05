# Codroid API

Python-клієнт для websocket-протоколу робота Codroid на основі захопленого трафіку `basics.har`.

Англомовна версія README:
https://github.com/profi-robotics/codroid-api/blob/main/README.md

## Встановлення (uv)

```bash
uv sync
```

Встановлення залежностей для прикладів (Dash):

```bash
uv sync --extra examples
```

## Конфігурація

Створіть файл `.env` (див. `.env.example`) і заповніть значення під вашого робота.

```bash
cp .env.example .env
```

Потрібно для більшості сценаріїв:

- `CODROID_TOKEN`
- `CODROID_USERNAME`
- `CODROID_USER_PASSWORD` (або `CODROID_USERCODE`)
- `CODROID_DEFAULT_PROJECT`, `CODROID_DEFAULT_TASK`, `CODROID_DEFAULT_LABEL` (для `run_project`)

Значення за замовчуванням у `CodroidSettings` відповідають типовій заводській конфігурації; перевизначте їх у `.env` для вашого робота.

## Використання

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

Той самий сценарій доступний як виконуваний скрипт:

```bash
uv run python examples/basic_usage.py
```

Приклад циклу точок:

```bash
uv run python examples/point_loop.py
```

## Постійна сесія

Використовуйте `RobotSession`, якщо потрібні довгоживучі user+robot websocket-зʼєднання
з керуванням моніторингом пози/DI (наприклад, для UI-застосунків):

```python
from codroid_api import RobotSession


async def main() -> None:
    session = RobotSession()
    await session.connect("ws://192.168.101.100:9000/")
    # Актуальний знімок пози в будь-який момент.
    posture = session.position_snapshot()
    print("Posture:", posture.x, posture.y, posture.z)
    await session.close()
```

## Допоміжні HTTP-методи для проєктів і точок

API містить HTTP-хелпери для керування файлами проєкту:

```python
# Створити новий проєкт без точок.
result = await api.create_project(label="NewProject")
project_id = result["id"]

# Додати або оновити точку (передайте повний dict точки).
await api.upsert_point(project_id, {"label": "P1", "postype": "cpos", "data": {...}})

# Видалити точку за id або label.
await api.delete_point(project_id, point_id="pt123")

# Прочитати/зберегти документ проєкту цілком.
project_doc = await api.read_project(project_id)
await api.save_project(project_doc)

# Видалити проєкт (використовується /robot/project/del?id=...).
await api.delete_project(project_id)
```

## Ручний jog та переміщення по точках

Сценарій створення точок використовує `jogMode/jogIndex/jogSpeed` для ручного руху та
`targetPosType/targetAPos/targetCPos` + команди 106/107 для переміщень до цілі:

```python
# Joint jog (вісь 1, додатний напрямок).
await robot_api.start_joint_jog(axis=1, direction=1)
await robot_api.send_command_heartbeat()
await robot_api.stop_jog()

# TCP jog у фреймі інструмента.
await robot_api.start_tcp_jog(axis=1, direction=-1, reference=robot_api.config.jog_references.tool)

# Joint target move (APOS/DAPOS).
apos = CodroidAPI.build_target_apos([j1, j2, j3, j4, j5, j6])
await robot_api.move_to_joint_target_linear(apos)

# Cartesian target move (CPOS/DCPOS) з контролем пози.
poscfg = CodroidAPI.build_poscfg(mode=4)
cpos = CodroidAPI.build_target_cpos(x, y, z, a, b, c, poscfg=poscfg)
await robot_api.move_to_cartesian_target_optimal(cpos)

# Калібрування координат (3 точки), прив'язане до user coordinate 5.
raw_points = [
    CodroidAPI.build_target_cpos(x1, y1, z1, a1, b1, c1),
    CodroidAPI.build_target_cpos(x2, y2, z2, a2, b2, c2),
    CodroidAPI.build_target_cpos(x3, y3, z3, a3, b3, c3),
]
calibrated_frame = await robot_api.coordinate_calibration(
    raw_points,
    coordinate_id=5,
    set_active_coordinate=True,
)
# Зберегти фрейм у слот user coordinate (поведінка як у HAR).
await robot_api.change_coordinate_parameter(coordinate_id=5, frame=calibrated_frame)
print("Calibrated user frame:", calibrated_frame)

# Див. також інтерактивний сценарій у examples/coordinate_calibration.py
await robot_api.close()
```

### Моніторинг кнопок/DI

Web UI опитує DI-порти кнопок pendant/flange через `IOManager/GetIOValue`.
Можна напряму стрімити DI-події:

```python
async with CodroidAPI(robot_config) as api:
    await api.robot_login()
    async for event in api.watch_di_changes():  # polls ports 0–15, 32, 33, 40–45
        print(event["label"], "->", event["value"])
```

Див. `examples/button_listener.py`.

### Переміщення до origin користувацької координати

Щоб перемістити робота до origin відкаліброваного user frame (наприклад, coordinate ID 6):

```python
async with CodroidAPI(robot_config) as api:
    await api.robot_login()
    await api.move_to_coordinate_origin(
        coordinate_id=6,
        linear=True,          # або False для optimal-path move
        hold_seconds=5.0,     # надсилати command heartbeats під час руху
    )
```

## Перегляд захоплення

Показати унікальні `action` із HAR:

```bash
uv run python examples/inspect_capture.py
```

## Відтворення HAR

Використайте loader захоплення, щоб відтворити websocket `send` із `basics.har` із overrides для user-полів:

```bash
uv run python examples/replay_capture.py
```

## Інтеграція OnRobot (попереднє мапування)

API включає попередню підтримку OnRobot `2FG7`, `VGC10` і `Soft Gripper`
через наявні `common.setparam` шляхи.
Ці значення за замовчуванням можуть відрізнятися між версіями прошивки контролера.

```python
from codroid_api import (
    CodroidAPI,
    OnRobotModel,
    OnRobotProfile,
)

# Застосувати профіль (model + payload + center-of-gravity + model params)
profile = OnRobotProfile(
    model=OnRobotModel.FG2_7,
    payload_kg=1.25,
    cog_x_m=0.0,
    cog_y_m=0.0,
    cog_z_m=0.08,
    params={"force_pct": 55, "speed_pct": 45, "width_mm": 25.0},
)
await robot_api.set_onrobot_profile(profile)

# Або налаштувати model/payload окремо
await robot_api.set_onrobot_model(OnRobotModel.VGC10)
await robot_api.set_onrobot_payload(payload_kg=0.8, cog_x_m=0.0, cog_y_m=0.0, cog_z_m=0.05)
```

Runtime-action хелпери:

```python
# 2FG7
await robot_api.onrobot_2fg7_open(width_mm=70.0, speed_pct=50)
await robot_api.onrobot_2fg7_close(width_mm=5.0, force_pct=60, speed_pct=40)

# VGC10
await robot_api.onrobot_vgc10_vacuum_on(vacuum_pct=80, channel=1)
await robot_api.onrobot_vgc10_vacuum_off(channel=1)
await robot_api.onrobot_vgc10_blow_off(duration_ms=250, channel=1)

# Soft Gripper
await robot_api.onrobot_soft_gripper_grip(pressure_pct=50, duration_ms=300)
await robot_api.onrobot_soft_gripper_release(duration_ms=300)
```

## Примітки

- Секрети та ідентифікатори завантажуються з `.env` через `CodroidSettings`.
- Додавайте нові коди команд у `codroid_api/commands.py` і використовуйте `CodroidAPI.set_robot_command()` / `set_param()`.
- Відповіді приходять асинхронно; використовуйте `listen()` або `recv()`.
- API протестовано лише на Codroid Web UI v.1.6.3c (відповідає захопленню HAR).
