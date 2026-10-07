"""Cloud relay: exposes the plant's API publicly over a WebSocket tunnel.

The plant accepts no inbound connections, so its synchronizer dials out and holds
a socket open here. Every HTTP request is turned into a command, sent down that
socket, and answered when the plant replies with the matching request_id.

There is exactly one plant connection: a second one replaces the first.
"""

import asyncio
import logging
import uuid

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RenderServer")

app = FastAPI()

# No wildcard: MSAL needs a secure context, so the frontend is served over HTTPS
# from CloudFront. Re-add the S3 website origins only if it is ever served
# directly from static hosting again.
ALLOWED_ORIGINS = [
    "https://d1gi8mg9znzd1h.cloudfront.net",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PLANT_RESPONSE_TIMEOUT_SECONDS = 12
# The analysis reads work through weeks of per-second readings; they outlast the
# plant's own wait for them (synchronizer ANALYSIS_TIMEOUT_SECONDS).
ANALYSIS_ACTIONS = {"asset_signal", "asset_drift", "asset_baseline", "asset_interval"}
ANALYSIS_TIMEOUT_SECONDS = 130
# Plant error strings carrying these codes are surfaced as the same HTTP status.
ERROR_STATUS_CODES = (401, 403, 404)


class ConnectionManager:
    """Owns the single plant socket and correlates requests with their replies."""

    def __init__(self):
        self.active_connection: WebSocket = None
        self.pending_requests = {}

    async def connect(self, websocket: WebSocket):
        """Accept the plant socket and make it the active one."""
        await websocket.accept()
        self.active_connection = websocket
        logger.info("--- PLANT CONNECTED ---")

    def disconnect(self, websocket: WebSocket):
        """Drop the socket and fail every request still waiting on it.

        Without this the pending futures would hang until their own timeout, so
        callers would wait the full window for a reply that can no longer arrive.
        """
        if self.active_connection != websocket:
            return

        self.active_connection = None
        logger.info("--- PLANT DISCONNECTED ---")
        for future in self.pending_requests.values():
            if not future.done():
                future.set_exception(
                    HTTPException(status_code=503, detail="Plant disconnected unexpectedly")
                )
        self.pending_requests.clear()

    async def send_command(self, command: dict):
        """Send one command to the plant and await its reply.

        Raises:
            HTTPException: 503 when no plant is connected, 504 on timeout, 500 on
            any other transport failure.
        """
        if not self.active_connection:
            raise HTTPException(status_code=503, detail="Plant is disconnected")

        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[request_id] = future
        command["request_id"] = request_id

        try:
            await self.active_connection.send_json(command)
            timeout = (ANALYSIS_TIMEOUT_SECONDS if command.get("accion") in ANALYSIS_ACTIONS
                       else PLANT_RESPONSE_TIMEOUT_SECONDS)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self.pending_requests.pop(request_id, None)
            raise HTTPException(status_code=504, detail="Plant took too long to respond")
        except HTTPException:
            self.pending_requests.pop(request_id, None)
            raise
        except Exception as e:
            self.pending_requests.pop(request_id, None)
            raise HTTPException(status_code=500, detail=f"Communication error: {str(e)}")

    def resolve_request(self, request_id, data):
        """Hand a plant reply to whoever is waiting for it."""
        future = self.pending_requests.pop(request_id, None)
        if future and not future.done():
            future.set_result(data)


manager = ConnectionManager()


@app.websocket("/ws_planta")
async def websocket_endpoint(websocket: WebSocket):
    """Hold the plant socket, routing replies and absorbing keep-alives."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if "request_id" in data:
                manager.resolve_request(data["request_id"], data["payload"])
            elif data.get("tipo") == "keep_alive":
                logger.info("Keep-Alive received from plant")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Critical socket error: {e}")
        manager.disconnect(websocket)


def _auth_headers(request: Request):
    """Forward only the Authorization header; the rest belong to the tunnel."""
    token = request.headers.get("authorization")
    return {"Authorization": token} if token else {}


def _raise_for_plant_error(result):
    """Translate an error payload from the plant into the matching HTTP status."""
    if not (isinstance(result, dict) and "error" in result):
        return result

    status_code = 500
    for code in ERROR_STATUS_CODES:
        if str(code) in str(result["error"]):
            status_code = code
            break
    raise HTTPException(status_code=status_code, detail=result.get("detail", result["error"]))


# path, method, action, sends id, sends query params, sends body, maps plant errors
#
# The last column preserves an existing split: GET and body-less POST routes map a
# plant error onto an HTTP status, while PUT/DELETE and the id-bearing POSTs return
# the raw {"error": ...} payload with a 200. Unifying it would change what the
# frontend receives on failure, so it is left as it was.
PROXY_ROUTES = [
    ("/integration/sap",                   "POST",    "sap_integration",           False, False, True,  True),
    ("/analysis/{id_asset}",               "GET",     "analisis",                  True,  True,  False, True),
    ("/events/{id_asset}",                 "GET",     "eventos",                   True,  True,  False, True),
    ("/assets",                            "GET",     "assets",                    False, True,  False, True),
    ("/dashboard/summary",                 "GET",     "dashboard",                 False, True,  False, True),
    ("/sensors/{id_asset}",                "GET",     "sensores",                  True,  True,  False, True),
    ("/configurations/{config_key}",       "GET",     "configuraciones",           True,  True,  False, True),
    ("/dashboard/lines",                   "GET",     "dashboard_lines",           False, True,  False, True),
    ("/dashboard/kpis",                    "GET",     "dashboard_kpis",            False, True,  False, True),
    ("/dashboard/bad-actors",              "GET",     "dashboard_bad_actors",      False, True,  False, True),
    ("/users",                             "GET",     "users",                     False, True,  False, True),
    ("/dashboard/lines/{id_line}/assets",  "GET",     "line_assets",               True,  True,  False, True),
    ("/users",                             "POST",    "create_user",               False, False, True,  True),
    ("/users/{user_id}",                   "PUT",     "update_user",               True,  False, True,  False),
    ("/users/{user_id}",                   "DELETE",  "delete_user",               True,  False, False, False),
    ("/auth/microsoft",                    "POST",    "microsoft_login",           False, False, True,  True),
    ("/auth/me",                           "GET",     "auth_me",                   False, True,  False, True),
    ("/alerts/events",                     "POST",    "register_alert_event",      False, False, True,  True),
    ("/alerts/tracking/kpis",              "GET",     "alerts_kpis",               False, True,  False, True),
    ("/alerts/tracking/counts",            "GET",     "alerts_counts",             False, True,  False, True),
    ("/alerts/tracking/history",           "GET",     "alerts_history",            False, True,  False, True),
    ("/alerts/tracking/users",             "GET",     "alerts_user_stats",         False, True,  False, True),
    ("/alerts/findings",                   "POST",    "register_alert_finding",    False, False, True,  True),
    ("/auth/setup-password",               "POST",    "setup_password",            False, False, True,  True),
    ("/assets/{id_asset}/runs",            "GET",     "get_asset_runs",            True,  True,  False, True),
    ("/assets/{id_asset}/traceability",    "GET",     "asset_traceability",        True,  True,  False, True),
    ("/assets/{id_asset}/signal",          "GET",     "asset_signal",              True,  True,  False, True),
    ("/assets/{id_asset}/drift",           "GET",     "asset_drift",               True,  True,  False, True),
    ("/assets/{id_asset}/baseline",        "GET",     "asset_baseline",            True,  True,  False, True),
    ("/assets/{id_asset}/interval",        "GET",     "asset_interval",            True,  True,  False, True),
    ("/users/{user_id}/resend-setup",      "POST",    "resend_setup",              True,  False, False, False),
    ("/production-lines",                  "GET",     "get_production_lines",      False, True,  False, True),
    ("/production-lines",                  "POST",    "create_production_line",    False, False, True,  True),
    ("/production-lines/{id_line}",        "PUT",     "update_production_line",    True,  False, True,  False),
    ("/production-lines/{id_line}",        "DELETE",  "delete_production_line",    True,  False, False, False),
    ("/assets/management",                 "GET",     "get_assets_management",     False, True,  False, True),
    ("/assets",                            "POST",    "create_asset",              False, False, True,  True),
    ("/assets/{id_asset}",                 "PUT",     "update_asset",              True,  False, True,  False),
    ("/assets/{id_asset}",                 "DELETE",  "delete_asset",              True,  False, False, False),
    ("/assets/{id_asset}/status",          "PUT",     "update_asset_status",       True,  False, True,  False),
    ("/sensor-config/{id_asset}",          "GET",     "get_sensor_config",         True,  True,  False, True),
    ("/sensor-config",                     "POST",    "create_sensor_config",      False, False, True,  True),
    ("/sensor-config/{id_variable}",       "PUT",     "update_sensor_config",      True,  False, True,  False),
    ("/sensor-config/{id_variable}",       "DELETE",  "delete_sensor_config",      True,  False, False, False),
    ("/model-config/{id_asset}",           "GET",     "get_model_config",          True,  True,  False, True),
    ("/model-config/{id_config}",          "PUT",     "update_model_config",       True,  False, True,  False),
    ("/model-config/{id_config}",          "DELETE",  "delete_model_config",       True,  False, False, False),
    ("/models/{id_asset}/metrics",         "GET",     "get_model_metrics",         True,  True,  False, True),
    ("/training/{id_asset}",               "POST",    "training_start",            True,  True,  False, False),
    ("/training/{id_asset}/status",        "GET",     "training_status",           True,  True,  False, True),
    ("/training/{id_asset}/suggest-range", "POST",    "training_suggest_range",    True,  True,  False, False),
    ("/training/{id_asset}/drift-status",  "GET",     "training_drift_status",     True,  True,  False, True),
    ("/training/{id_asset}/status",        "DELETE",  "training_delete_status",    True,  False, False, False),
]

def _register_proxy_route(path, method, action, sends_id, sends_params, sends_body, maps_errors):
    """Register one tunnelled endpoint.

    The handler takes only ``request``: the path parameter is read from
    ``request.path_params``, so one signature serves every path shape.
    """

    async def handler(request: Request):
        """Turn this request into a plant command and return the reply."""
        command = {"accion": action, "headers": _auth_headers(request)}

        if sends_id:
            command["id_asset"] = next(iter(request.path_params.values()), None)
        if sends_params:
            command["params"] = dict(request.query_params)
        if sends_body:
            command["payload"] = await request.json()

        if not maps_errors:
            return await manager.send_command(command)

        try:
            return _raise_for_plant_error(await manager.send_command(command))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    handler.__name__ = action
    app.add_api_route(path, handler, methods=[method], name=action)


for _route in PROXY_ROUTES:
    _register_proxy_route(*_route)


@app.post("/auth/login")
async def login(request: Request):
    """Sign in. Declared separately: the body is an OAuth2 form, not JSON."""
    form_data = await request.form()
    command = {
        "accion": "login",
        "payload": {
            "username": form_data.get("username"),
            "password": form_data.get("password"),
        },
        "headers": _auth_headers(request),
    }
    try:
        return _raise_for_plant_error(await manager.send_command(command))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def root():
    """Liveness probe."""
    return {"status": "SOCKET SERVER ARMORED V5"}
