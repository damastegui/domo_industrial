from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import uuid
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RenderServer")

app = FastAPI()

#app.add_middleware(
    #CORSMiddleware, allow_origins=["*"], allow_credentials=True, 
    #allow_methods=["*"], allow_headers=["*"]
#)
ORIGINES_PERMITIDOS = [
    # Reemplazado por CloudFront (HTTPS) para poder usar Microsoft Entra ID (MSAL requiere secure context).
    # Descomentar solo si se vuelve a servir el frontend directo desde el static website hosting de S3.
    # "http://domo-dashboard-planta-2026.s3-website-us-east-1.amazonaws.com",
    # "https://domo-dashboard-planta-2026.s3-website-us-east-1.amazonaws.com",
    "https://d1gi8mg9znzd1h.cloudfront.net",
    "http://localhost:5173",
    "http://127.0.0.1:5173"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINES_PERMITIDOS,   # Sin comodín universal
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
class ConnectionManager:
    def __init__(self):
        self.active_connection: WebSocket = None
        self.pending_requests = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connection = websocket
        logger.info("--- PLANT CONNECTED ---")

    def disconnect(self, websocket: WebSocket):
        if self.active_connection == websocket:
            self.active_connection = None
            logger.info("--- PLANT DISCONNECTED ---")
            for req_id, future in self.pending_requests.items():
                if not future.done():
                    future.set_exception(HTTPException(status_code=503, detail="Plant disconnected unexpectedly"))
            self.pending_requests.clear()

    async def send_command(self, command: dict):
        if not self.active_connection:
            raise HTTPException(status_code=503, detail="Plant is disconnected")
        
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[request_id] = future
        
        command["request_id"] = request_id
        
        try:
            await self.active_connection.send_json(command)
            return await asyncio.wait_for(future, timeout=12.0)
        except asyncio.TimeoutError:
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            raise HTTPException(status_code=504, detail="Plant took too long to respond")
        except HTTPException as he:
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            raise he
        except Exception as e:
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            raise HTTPException(status_code=500, detail=f"Communication error: {str(e)}")

    def resolve_request(self, request_id, data):
        if request_id in self.pending_requests:
            if not self.pending_requests[request_id].done():
                self.pending_requests[request_id].set_result(data)
            del self.pending_requests[request_id]

manager = ConnectionManager()

@app.websocket("/ws_planta")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            
            if "request_id" in data:
                manager.resolve_request(data["request_id"], data["payload"])
            elif "tipo" in data and data["tipo"] == "keep_alive":
                logger.info("Keep-Alive received from plant")
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Critical socket error: {e}")
        manager.disconnect(websocket)

# --- Funciones procesadoras ---

async def process_command(accion: str, id_asset: str = None, request: Request = None):
    try:
        params = dict(request.query_params) if request else {}
        headers = {}
        if request and "authorization" in request.headers:
            headers["Authorization"] = request.headers["authorization"]

        command = {"accion": accion, "params": params, "headers": headers}
        if id_asset:
            command["id_asset"] = id_asset
            
        result = await manager.send_command(command)
        
        if isinstance(result, dict) and "error" in result:
            status_code = 500
            if "401" in str(result["error"]): status_code = 401
            elif "403" in str(result["error"]): status_code = 403
            elif "404" in str(result["error"]): status_code = 404
            raise HTTPException(status_code=status_code, detail=result.get("detail", result["error"]))
            
        return result
    except HTTPException as he: raise he
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

async def process_post_command(accion: str, payload: dict = None, request: Request = None):
    try:
        headers = {}
        if request and "authorization" in request.headers:
            headers["Authorization"] = request.headers["authorization"]

        command = {"accion": accion, "payload": payload, "headers": headers}
        result = await manager.send_command(command)
        
        if isinstance(result, dict) and "error" in result:
            status_code = 500
            if "401" in str(result["error"]): status_code = 401
            elif "403" in str(result["error"]): status_code = 403
            elif "404" in str(result["error"]): status_code = 404
            raise HTTPException(status_code=status_code, detail=result.get("detail", result["error"]))
            
        return result
    except HTTPException as he: raise he
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

# --- Endpoints ---

@app.post("/auth/login")
async def login(request: Request):
    form_data = await request.form()
    payload = {
        "username": form_data.get("username"),
        "password": form_data.get("password")
    }
    return await process_post_command("login", payload, request)

@app.post("/integration/sap")
async def create_sap_order(request: Request):
    payload = await request.json()
    return await process_post_command("sap_integration", payload, request)

@app.get("/raw_physical_history/{id_asset}")
async def get_history(id_asset: str, request: Request):
    return await process_command("historial", id_asset, request)

@app.get("/analysis/{id_asset}")
async def get_analysis(id_asset: str, request: Request):
    return await process_command("analisis", id_asset, request)

@app.get("/events/{id_asset}")
async def get_events(id_asset: str, request: Request):
    return await process_command("eventos", id_asset, request)

@app.get("/assets")
async def get_assets(request: Request):
    return await process_command("assets", request=request)

@app.get("/dashboard/summary")
async def get_dashboard(request: Request):
    return await process_command("dashboard", request=request)

@app.get("/sensors/{id_asset}")
async def get_sensors(id_asset: str, request: Request):
    return await process_command("sensores", id_asset, request)

@app.get("/configurations/{config_key}")
async def get_config(config_key: str, request: Request):
    return await process_command("configuraciones", config_key, request)

@app.get("/dashboard/lines")
async def get_dashboard_lines(request: Request):
    return await process_command("dashboard_lines", request=request)

@app.get("/dashboard/kpis")
async def get_dashboard_kpis(request: Request):
    return await process_command("dashboard_kpis", request=request)

@app.get("/dashboard/bad-actors")
async def get_bad_actors(request: Request):
    return await process_command("dashboard_bad_actors", request=request)

@app.get("/users")
async def get_users(request: Request):
    return await process_command("users", request=request)

@app.get("/dashboard/lines/{id_line}/assets")
async def get_line_assets(id_line: str, request: Request):
    return await process_command("line_assets", id_line, request)

@app.post("/users")
async def create_user(request: Request):
    payload = await request.json()
    return await process_post_command("create_user", payload, request)

@app.put("/users/{user_id}")
async def update_user(user_id: str, request: Request):
    payload = await request.json()
    try:
        headers = {}
        if "authorization" in request.headers:
            headers["Authorization"] = request.headers["authorization"]
        command = {"accion": "update_user", "payload": payload, "headers": headers, "id_asset": user_id}
        return await manager.send_command(command)
    except HTTPException as he: raise he
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.delete("/users/{user_id}")
async def delete_user(user_id: str, request: Request):
    try:
        headers = {}
        if "authorization" in request.headers:
            headers["Authorization"] = request.headers["authorization"]
        command = {"accion": "delete_user", "headers": headers, "id_asset": user_id}
        return await manager.send_command(command)
    except HTTPException as he: raise he
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.post("/auth/microsoft")
async def microsoft_login(request: Request):
    return await process_command("microsoft_login", request=request)

@app.get("/auth/me")
async def get_me(request: Request):
    return await process_command("auth_me", request=request)

@app.post("/alerts/events")
async def register_alert_event(request: Request):
    payload = await request.json()
    return await process_post_command("register_alert_event", payload, request)

@app.get("/alerts/tracking/kpis")
async def get_alerts_kpis(request: Request):
    return await process_command("alerts_kpis", request=request)

@app.get("/alerts/tracking/counts")
async def get_alerts_counts(request: Request):
    return await process_command("alerts_counts", request=request)

@app.get("/alerts/tracking/history")
async def get_alerts_history(request: Request):
    return await process_command("alerts_history", request=request)

@app.get("/alerts/tracking/users")
async def get_alerts_user_stats(request: Request):
    return await process_command("alerts_user_stats", request=request)

@app.post("/alerts/findings")
async def register_alert_finding(request: Request):
    payload = await request.json()
    return await process_post_command("register_alert_finding", payload, request)

@app.post("/auth/setup-password")
async def setup_password(request: Request):
    payload = await request.json()
    return await process_post_command("setup_password", payload, request)

@app.get("/assets/{id_asset}/runs")
async def get_asset_runs(id_asset: str, request: Request):
    return await process_command("get_asset_runs", id_asset, request)

@app.post("/users/{user_id}/resend-setup")
async def resend_setup(user_id: str, request: Request):
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "resend_setup", "id_asset": user_id, "headers": headers}
    return await manager.send_command(command)

@app.get("/production-lines")
async def get_production_lines(request: Request):
    return await process_command("get_production_lines", request=request)

@app.post("/production-lines")
async def create_production_line(request: Request):
    payload = await request.json()
    return await process_post_command("create_production_line", payload, request)

@app.put("/production-lines/{id_line}")
async def update_production_line(id_line: str, request: Request):
    payload = await request.json()
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "update_production_line", "payload": payload, "headers": headers, "id_asset": id_line}
    return await manager.send_command(command)

@app.delete("/production-lines/{id_line}")
async def delete_production_line(id_line: str, request: Request):
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "delete_production_line", "headers": headers, "id_asset": id_line}
    return await manager.send_command(command)

@app.get("/assets/management")
async def get_assets_management(request: Request):
    return await process_command("get_assets_management", request=request)

@app.post("/assets")
async def create_asset(request: Request):
    payload = await request.json()
    return await process_post_command("create_asset", payload, request)

@app.put("/assets/{id_asset}")
async def update_asset(id_asset: str, request: Request):
    payload = await request.json()
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "update_asset", "payload": payload, "headers": headers, "id_asset": id_asset}
    return await manager.send_command(command)

@app.delete("/assets/{id_asset}")
async def delete_asset_endpoint(id_asset: str, request: Request):
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "delete_asset", "headers": headers, "id_asset": id_asset}
    return await manager.send_command(command)

@app.put("/assets/{id_asset}/status")
async def update_asset_status(id_asset: str, request: Request):
    payload = await request.json()
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "update_asset_status", "payload": payload, "headers": headers, "id_asset": id_asset}
    return await manager.send_command(command)

@app.get("/sensor-config/{id_asset}")
async def get_sensor_config(id_asset: str, request: Request):
    return await process_command("get_sensor_config", id_asset, request)

@app.post("/sensor-config")
async def create_sensor_config(request: Request):
    payload = await request.json()
    return await process_post_command("create_sensor_config", payload, request)

@app.put("/sensor-config/{id_variable}")
async def update_sensor_config(id_variable: str, request: Request):
    payload = await request.json()
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "update_sensor_config", "payload": payload, "headers": headers, "id_asset": id_variable}
    return await manager.send_command(command)

@app.delete("/sensor-config/{id_variable}")
async def delete_sensor_config(id_variable: str, request: Request):
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "delete_sensor_config", "headers": headers, "id_asset": id_variable}
    return await manager.send_command(command)

@app.get("/model-config/{id_asset}")
async def get_model_config(id_asset: str, request: Request):
    return await process_command("get_model_config", id_asset, request)

@app.post("/model-config")
async def create_model_config(request: Request):
    payload = await request.json()
    return await process_post_command("create_model_config", payload, request)

@app.put("/model-config/{id_config}")
async def update_model_config(id_config: str, request: Request):
    payload = await request.json()
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "update_model_config", "payload": payload, "headers": headers, "id_asset": id_config}
    return await manager.send_command(command)

@app.delete("/model-config/{id_config}")
async def delete_model_config(id_config: str, request: Request):
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "delete_model_config", "headers": headers, "id_asset": id_config}
    return await manager.send_command(command)

@app.get("/models/{id_asset}/metrics")
async def get_model_metrics(id_asset: str, request: Request):
    return await process_command("get_model_metrics", id_asset, request)

@app.post("/training/{id_asset}")
async def training_start(id_asset: str, request: Request):
    params = dict(request.query_params)
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "training_start", "params": params, "headers": headers, "id_asset": id_asset}
    return await manager.send_command(command)

@app.get("/training/{id_asset}/status")
async def training_status(id_asset: str, request: Request):
    return await process_command("training_status", id_asset, request)

@app.post("/training/{id_asset}/suggest-range")
async def training_suggest_range(id_asset: str, request: Request):
    params = dict(request.query_params)
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "training_suggest_range", "params": params, "headers": headers, "id_asset": id_asset}
    return await manager.send_command(command)

@app.get("/training/{id_asset}/drift-status")
async def training_drift_status(id_asset: str, request: Request):
    return await process_command("training_drift_status", id_asset, request)

@app.delete("/training/{id_asset}/status")
async def training_delete_status(id_asset: str, request: Request):
    headers = {}
    if "authorization" in request.headers: headers["Authorization"] = request.headers["authorization"]
    command = {"accion": "training_delete_status", "headers": headers, "id_asset": id_asset}
    return await manager.send_command(command)

@app.get("/training/{id_asset}/histograms")
async def training_histograms(id_asset: str, request: Request):
    return await process_command("training_histograms", id_asset, request)

@app.get("/training/{id_asset}/temporal-plot")
async def training_temporal_plot(id_asset: str, request: Request):
    return await process_command("training_temporal_plot", id_asset, request)

@app.get("/training/{id_asset}/calibration")
async def training_calibration(id_asset: str, request: Request):
    return await process_command("training_calibration", id_asset, request)

@app.get("/")
def root(): 
    return {"status": "SOCKET SERVER ARMORED V5"}
