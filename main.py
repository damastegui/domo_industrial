from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import uuid
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, 
    allow_methods=["*"], allow_headers=["*"]
)

class ConnectionManager:
    def __init__(self):
        self.active_connection: WebSocket = None
        self.pending_requests = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connection = websocket
        print("--- PLANTA CONECTADA ---")

    def disconnect(self, websocket: WebSocket):
        self.active_connection = None
        print("--- PLANTA DESCONECTADA ---")

    async def send_command(self, command: dict):
        if not self.active_connection:
            raise HTTPException(status_code=503, detail="Planta desconectada")
        
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[request_id] = future
        
        command["request_id"] = request_id
        await self.active_connection.send_json(command)
        
        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            del self.pending_requests[request_id]
            raise HTTPException(status_code=504, detail="Tiempo de espera agotado en Planta")

    def resolve_request(self, request_id, data):
        if request_id in self.pending_requests:
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
            
            elif "tipo" in data:
                pass
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/historial_fisico_raw/{id_equipo}")
async def get_historial(id_equipo: str, request: Request):
    params = dict(request.query_params)
    
    respuesta = await manager.send_command({
        "accion": "historial",
        "id_equipo": id_equipo,
        "params": params
    })
    return respuesta

@app.get("/analisis/{id_equipo}")
async def get_analisis(id_equipo: str, request: Request):
    params = dict(request.query_params)
    respuesta = await manager.send_command({
        "accion": "analisis",
        "id_equipo": id_equipo,
        "params": params
    })
    return respuesta

@app.get("/eventos/{id_equipo}")
async def get_eventos(id_equipo: str, request: Request):
    params = dict(request.query_params)
    respuesta = await manager.send_command({
        "accion": "eventos",
        "id_equipo": id_equipo,
        "params": params
    })
    return respuesta

@app.get("/equipos")
async def get_equipos():
    return await manager.send_command({"accion": "equipos"})

@app.get("/dashboard/resumen")
async def get_dashboard():
    return await manager.send_command({"accion": "dashboard"})

@app.get("/sensores/{id_equipo}")
async def get_sensores(id_equipo: str):
    return await manager.send_command({"accion": "sensores", "id_equipo": id_equipo})

@app.get("/")
def raiz(): return {"estado": "SOCKET SERVER ACTIVO"}
