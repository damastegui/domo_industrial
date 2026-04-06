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
            raise HTTPException(status_code=503, detail="Plant is disconnected (Waiting for reconnection)")
        
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
            raise HTTPException(status_code=504, detail="Plant took too long to respond")
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
                logger.info("Keep-Alive recibido de planta")
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Critical socket error: {e}")
        manager.disconnect(websocket)

async def process_command(action: str, id_asset: str = None, request: Request = None):
    try:
        params = dict(request.query_params) if request else {}
        command = {"action": action, "params": params}
        if id_asset:
            comando["id_asset"] = id_asset
            
        return await manager.send_command(comando)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error processing {action}: {e}")
        raise HTTPException(status_code=500, detail="Internal cloud server error")

@app.get("/raw_physical_history/{id_asset}")
async def get_history(id_asset: str, request: Request):
    return await process_command("history", id_asset, request)

@app.get("/analysis/{id_asset}")
async def get_analysis(id_asset: str, request: Request):
    return await process_command("analysis", id_asset, request)

@app.get("/events/{id_asset}")
async def get_events(id_asset: str, request: Request):
    return await process_command("events", id_asset, request)

@app.get("/assets")
async def get_assets():
    return await process_command("assets")

@app.get("/dashboard/summary")
async def get_dashboard():
    return await process_command("dashboard")

@app.get("/sensors/{id_asset}")
async def get_sensors(id_asset: str):
    return await process_command("sensors", id_asset)

@app.get("/")
def root(): return {"status": "SOCKET SERVER ARMORED V2"}
