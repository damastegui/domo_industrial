from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import uuid
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RenderServer")

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
        logger.info("--- PLANTA CONECTADA ---")

    def disconnect(self, websocket: WebSocket):
        if self.active_connection == websocket:
            self.active_connection = None
            logger.info("--- PLANTA DESCONECTADA ---")
            for req_id, future in self.pending_requests.items():
                if not future.done():
                    future.set_exception(HTTPException(status_code=503, detail="Planta se desconectó repentinamente"))
            self.pending_requests.clear()

    async def send_command(self, command: dict):
        if not self.active_connection:
            raise HTTPException(status_code=503, detail="La planta está desconectada (Esperando reconexión...)")
        
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[request_id] = future
        
        command["request_id"] = request_id
        
        try:
            await self.active_connection.send_json(command)
            return await asyncio.wait_for(future, timeout=8.0)
        except asyncio.TimeoutError:
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            raise HTTPException(status_code=504, detail="La planta tardó demasiado en responder")
        except Exception as e:
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            raise HTTPException(status_code=500, detail=f"Error de comunicación: {str(e)}")

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
                logger.info("Keep-Alive recibido de planta")
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Error crítico en socket: {e}")
        manager.disconnect(websocket)

async def procesar_comando(accion: str, id_equipo: str = None, request: Request = None):
    try:
        params = dict(request.query_params) if request else {}
        comando = {"accion": accion, "params": params}
        if id_equipo:
            comando["id_equipo"] = id_equipo
            
        return await manager.send_command(comando)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error procesando {accion}: {e}")
        raise HTTPException(status_code=500, detail="Error interno en el servidor nube")

@app.get("/historial_fisico_raw/{id_equipo}")
async def get_historial(id_equipo: str, request: Request):
    return await procesar_comando("historial", id_equipo, request)

@app.get("/analisis/{id_equipo}")
async def get_analisis(id_equipo: str, request: Request):
    return await procesar_comando("analisis", id_equipo, request)

@app.get("/eventos/{id_equipo}")
async def get_eventos(id_equipo: str, request: Request):
    return await procesar_comando("eventos", id_equipo, request)

@app.get("/equipos")
async def get_equipos():
    return await procesar_comando("equipos")

@app.get("/dashboard/resumen")
async def get_dashboard():
    return await procesar_comando("dashboard")

@app.get("/sensores/{id_equipo}")
async def get_sensores(id_equipo: str):
    return await procesar_comando("sensores", id_equipo)

@app.get("/")
def raiz(): return {"estado": "SOCKET SERVER BLINDADO V2"}
