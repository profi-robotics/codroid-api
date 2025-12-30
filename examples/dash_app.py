import asyncio
import threading
import time
from typing import Optional

from dash import Dash, Input, Output, State, callback_context, dcc, html

from codroid_api import CodroidAPI, CodroidSettings


class RobotSession:
    def __init__(self, settings: CodroidSettings) -> None:
        self._settings = settings
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._ready = threading.Event()
        self._error: Optional[str] = None
        self._user_api: Optional[CodroidAPI] = None
        self._robot_api: Optional[CodroidAPI] = None
        self._move_task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._thread.start()

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def error(self) -> Optional[str]:
        return self._error

    def run(self, coro: asyncio.Future) -> None:
        if not self.ready:
            raise RuntimeError("Robot session is not ready.")
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect())
        self._loop.run_forever()

    async def _connect(self) -> None:
        try:
            user_config = self._settings.build_user_config()
            robot_config = self._settings.build_robot_config()

            self._user_api = CodroidAPI(user_config)
            self._robot_api = CodroidAPI(robot_config)

            await self._user_api.connect()
            if user_config.user_password:
                await self._user_api.ws_login_with_password()
            else:
                await self._user_api.ws_login()

            await self._robot_api.connect()
            await self._robot_api.robot_login()
            await self._robot_api.read_config()
            await self._robot_api.read_system_data()
            await self._robot_api.read_global_data()
            await self._robot_api.get_io_info()
            await self._robot_api.set_language(user_config.default_language)

            self._ready.set()
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)

    async def start_project(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.run_project()

    async def stop_project(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.stop_project()

    async def power_on(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.power_on()

    async def power_off(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.power_off()

    async def set_manual_mode(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.set_manual_mode()

    async def set_auto_mode(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.set_auto_mode()

    async def start_hold_move(self, command: int) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self.stop_hold_move()
        await self._robot_api.set_robot_command(command)
        self._move_task = asyncio.create_task(self._move_heartbeat())

    async def stop_hold_move(self) -> None:
        if self._move_task:
            self._move_task.cancel()
            try:
                await self._move_task
            except asyncio.CancelledError:
                pass
            self._move_task = None
        if self._robot_api:
            await self._robot_api.stop_command()

    async def _move_heartbeat(self) -> None:
        while True:
            await self._robot_api.send_command_heartbeat()
            await asyncio.sleep(0.5)

    async def start_hold_move_home(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self.start_hold_move(self._robot_api.config.commands.move_home)

    async def start_hold_move_safe(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self.start_hold_move(self._robot_api.config.commands.move_safe)

    async def start_hold_move_candle(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self.start_hold_move(self._robot_api.config.commands.move_candle)

    async def start_hold_move_package(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self.start_hold_move(self._robot_api.config.commands.move_package)

    async def move_home(self, hold_seconds: float = 0.0) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.move_home(hold_seconds=hold_seconds)

    async def move_safe(self, hold_seconds: float = 0.0) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.move_safe(hold_seconds=hold_seconds)

    async def move_candle(self, hold_seconds: float = 0.0) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.move_candle(hold_seconds=hold_seconds)

    async def move_package(self, hold_seconds: float = 0.0) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.move_package(hold_seconds=hold_seconds)

    async def set_speed_multiplier(self, rate: float) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.set_speed_multiplier(rate)

    async def set_coordinate(self, coordinate_id: int) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.set_current_coordinate_id(coordinate_id)

    async def set_jog_reference(self, reference: int) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.set_jog_reference(reference)

    async def set_variable_state(self, state: int) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.set_variable_state(state)

    async def get_record_flag(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.get_record_flag()

    async def set_record_flag(self, enabled: bool) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.set_record_flag(enabled)

    async def get_trajectory_list(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.get_trajectory_list()

    async def get_trajectory_dir(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.get_trajectory_dir()

    async def get_log_file_list(self) -> None:
        if not self._robot_api:
            raise RuntimeError("Robot API is not connected.")
        await self._robot_api.get_log_file_list()


session: Optional[RobotSession] = None
settings = CodroidSettings()
if settings.user_password or settings.usercode:
    session = RobotSession(settings)
    session.start()


app = Dash(__name__)
app.layout = html.Div(
    [
        html.H2("Profi Robotics Reverse Engineered Codroid Control Panel"),
        html.Div(
            [
                html.Button("Power On", id="power-on", n_clicks=0),
                html.Button("Power Off", id="power-off", n_clicks=0),
                html.Button("Manual Mode", id="mode-manual", n_clicks=0),
                html.Button("Automatic Mode", id="mode-auto", n_clicks=0),
            ],
            style={"display": "flex", "gap": "12px", "flexWrap": "wrap"},
        ),
        html.Div(
            [
                html.Button("Start Project", id="project-start", n_clicks=0),
                html.Button("Stop Project", id="project-stop", n_clicks=0),
            ],
            style={"display": "flex", "gap": "12px", "marginTop": "12px"},
        ),
        html.Hr(),
        html.Div(
            [
                html.Button("Move Home", id="move-home", n_clicks=0),
                html.Button("Move Safe", id="move-safe", n_clicks=0),
                html.Button("Move Candle", id="move-candle", n_clicks=0),
                html.Button("Move Package", id="move-package", n_clicks=0),
            ],
            style={"display": "flex", "gap": "12px", "flexWrap": "wrap"},
        ),
        html.Div(
            [
                dcc.Input(
                    id="speed-multiplier",
                    type="number",
                    min=0,
                    max=1,
                    step=0.01,
                    value=1.0,
                    style={"width": "140px"},
                ),
                html.Button("Set Speed Multiplier", id="set-speed", n_clicks=0),
                dcc.Input(
                    id="coordinate-id",
                    type="number",
                    min=0,
                    step=1,
                    value=0,
                    style={"width": "120px"},
                ),
                html.Button("Set Coordinate", id="set-coordinate", n_clicks=0),
            ],
            style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginTop": "12px"},
        ),
        html.Div(
            [
                dcc.Input(
                    id="jog-reference",
                    type="number",
                    min=0,
                    step=1,
                    value=0,
                    style={"width": "120px"},
                ),
                html.Button("Set Jog Reference", id="set-jog", n_clicks=0),
                dcc.Input(
                    id="variable-state",
                    type="number",
                    min=0,
                    step=1,
                    value=0,
                    style={"width": "120px"},
                ),
                html.Button("Set Variable State", id="set-variable", n_clicks=0),
            ],
            style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginTop": "12px"},
        ),
        html.Div(
            [
                html.Button("Get Record Flag", id="get-record-flag", n_clicks=0),
                html.Button("Start Record", id="set-record-on", n_clicks=0),
                html.Button("Stop Record", id="set-record-off", n_clicks=0),
                html.Button("Get Trajectory List", id="get-trajectory-list", n_clicks=0),
                html.Button("Get Trajectory Dir", id="get-trajectory-dir", n_clicks=0),
                html.Button("Get Log Files", id="get-log-files", n_clicks=0),
            ],
            style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginTop": "12px"},
        ),
        dcc.Store(id="active-move", data=None),
        dcc.Store(id="action-log", data=[]),
        html.Div(id="status", style={"marginTop": "16px"}),
        html.Pre(id="log", style={
                 "marginTop": "12px", "whiteSpace": "pre-wrap"}),
    ],
    style={"fontFamily": "Arial, sans-serif", "padding": "20px"},
)


def _append_log(log, message: str):
    if not isinstance(log, list):
        log = []
    log.append(message)
    return log[-50:]


@app.callback(
    Output("action-log", "data"),
    Output("active-move", "data"),
    Input("power-on", "n_clicks"),
    Input("power-off", "n_clicks"),
    Input("mode-manual", "n_clicks"),
    Input("mode-auto", "n_clicks"),
    Input("project-start", "n_clicks"),
    Input("project-stop", "n_clicks"),
    Input("move-home", "n_clicks"),
    Input("move-safe", "n_clicks"),
    Input("move-candle", "n_clicks"),
    Input("move-package", "n_clicks"),
    Input("set-speed", "n_clicks"),
    Input("set-coordinate", "n_clicks"),
    Input("set-jog", "n_clicks"),
    Input("set-variable", "n_clicks"),
    Input("get-record-flag", "n_clicks"),
    Input("set-record-on", "n_clicks"),
    Input("set-record-off", "n_clicks"),
    Input("get-trajectory-list", "n_clicks"),
    Input("get-trajectory-dir", "n_clicks"),
    Input("get-log-files", "n_clicks"),
    State("speed-multiplier", "value"),
    State("coordinate-id", "value"),
    State("jog-reference", "value"),
    State("variable-state", "value"),
    State("active-move", "data"),
    State("action-log", "data"),
    prevent_initial_call=True,
)
def handle_action(
    _power_on,  # noqa: D401
    _power_off,
    _mode_manual,
    _mode_auto,
    _project_start,
    _project_stop,
    _move_home,
    _move_safe,
    _move_candle,
    _move_package,
    _set_speed,
    _set_coordinate,
    _set_jog,
    _set_variable,
    _get_record_flag,
    _set_record_on,
    _set_record_off,
    _get_trajectory_list,
    _get_trajectory_dir,
    _get_log_files,
    speed_multiplier,
    coordinate_id,
    jog_reference,
    variable_state,
    active_move,
    log,
):
    trigger = callback_context.triggered[0]["prop_id"].split(".")[0]
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    def result(message: str, next_active_move=active_move):
        return _append_log(log, message), next_active_move

    if session is None:
        return result(
            f"{timestamp} | {trigger}: set CODROID_USER_PASSWORD or CODROID_USERCODE to connect."
        )

    if session.error:
        return result(f"{timestamp} | {trigger}: session error: {session.error}")

    if not session.ready:
        return result(f"{timestamp} | {trigger}: session not ready yet.")

    if trigger == "power-on":
        session.run(session.set_auto_mode())
        return result(f"{timestamp} | power on -> auto mode")
    if trigger == "power-off":
        session.run(session.set_manual_mode())
        return result(f"{timestamp} | power off -> manual mode")
    if trigger == "mode-manual":
        session.run(session.power_on())
        return result(f"{timestamp} | manual mode -> power on")
    if trigger == "mode-auto":
        session.run(session.power_off())
        return result(f"{timestamp} | auto mode -> power off")
    if trigger == "project-start":
        session.run(session.start_project())
        return result(f"{timestamp} | start project")
    if trigger == "project-stop":
        session.run(session.stop_project())
        return result(f"{timestamp} | stop project")
    if trigger == "move-home":
        if active_move == "move-home":
            session.run(session.stop_hold_move())
            return result(f"{timestamp} | stop move home", None)
        session.run(session.start_hold_move_home())
        return result(f"{timestamp} | start move home (hold)", "move-home")
    if trigger == "move-safe":
        if active_move == "move-safe":
            session.run(session.stop_hold_move())
            return result(f"{timestamp} | stop move safe", None)
        session.run(session.start_hold_move_safe())
        return result(f"{timestamp} | start move safe (hold)", "move-safe")
    if trigger == "move-candle":
        if active_move == "move-candle":
            session.run(session.stop_hold_move())
            return result(f"{timestamp} | stop move candle", None)
        session.run(session.start_hold_move_candle())
        return result(f"{timestamp} | start move candle (hold)", "move-candle")
    if trigger == "move-package":
        if active_move == "move-package":
            session.run(session.stop_hold_move())
            return result(f"{timestamp} | stop move package", None)
        session.run(session.start_hold_move_package())
        return result(f"{timestamp} | start move package (hold)", "move-package")
    if trigger == "set-speed":
        rate = float(speed_multiplier or 0)
        session.run(session.set_speed_multiplier(rate))
        return result(f"{timestamp} | set speed multiplier {rate}")
    if trigger == "set-coordinate":
        cid = int(coordinate_id or 0)
        session.run(session.set_coordinate(cid))
        return result(f"{timestamp} | set coordinate {cid}")
    if trigger == "set-jog":
        ref = int(jog_reference or 0)
        session.run(session.set_jog_reference(ref))
        return result(f"{timestamp} | set jog reference {ref}")
    if trigger == "set-variable":
        state = int(variable_state or 0)
        session.run(session.set_variable_state(state))
        return result(f"{timestamp} | set variable state {state}")
    if trigger == "get-record-flag":
        session.run(session.get_record_flag())
        return result(f"{timestamp} | get record flag")
    if trigger == "set-record-on":
        session.run(session.set_record_flag(True))
        return result(f"{timestamp} | set record flag on")
    if trigger == "set-record-off":
        session.run(session.set_record_flag(False))
        return result(f"{timestamp} | set record flag off")
    if trigger == "get-trajectory-list":
        session.run(session.get_trajectory_list())
        return result(f"{timestamp} | get trajectory list")
    if trigger == "get-trajectory-dir":
        session.run(session.get_trajectory_dir())
        return result(f"{timestamp} | get trajectory dir")
    if trigger == "get-log-files":
        session.run(session.get_log_file_list())
        return result(f"{timestamp} | get log files")

    return result(f"{timestamp} | {trigger}: not wired")


@app.callback(
    Output("status", "children"),
    Output("log", "children"),
    Input("action-log", "data"),
)
def render_status(log):
    if session is None:
        status = "Status: not connected (set CODROID_USER_PASSWORD or CODROID_USERCODE)."
    elif session.error:
        status = f"Status: error ({session.error})"
    elif session.ready:
        status = "Status: connected."
    else:
        status = "Status: connecting..."
    return status, "\n".join(log or [])


if __name__ == "__main__":
    app.run(debug=True)
