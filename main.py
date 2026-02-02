from fastapi import FastAPI, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from typing import Any, Dict

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, 
    allow_methods=["*"], allow_headers=["*"]
)

CACHE_DATOS = {
    "dashboard": [], "equipos": [], 
    "sensores": {}, "analisis": {}, 
    "eventos": {}, "historial_fisico": {}
}

INTENCIONES = {}

@app.get("/")
def raiz(): return {"estado": "Espejo Total Activo"}

@app.get("/dashboard/resumen")
def get_dashboard(): return CACHE_DATOS["dashboard"]

@app.get("/equipos")
def get_equipos(): return CACHE_DATOS["equipos"]

@app.get("/sensores/{id_equipo}")
def get_sensores(id_equipo: str): return CACHE_DATOS["sensores"].get(id_equipo, [])

@app.get("/analisis/{id_equipo}")
def get_analisis(id_equipo: str, request: Request):
    INTENCIONES[f"analisis_{id_equipo}"] = str(request.query_params)
    return CACHE_DATOS["analisis"].get(id_equipo, {"datos": []})

@app.get("/eventos/{id_equipo}")
def get_eventos(id_equipo: str, request: Request):
    INTENCIONES[f"eventos_{id_equipo}"] = str(request.query_params)
    return CACHE_DATOS["eventos"].get(id_equipo, [])

@app.get("/historial_fisico_raw/{id_equipo}")
def get_historial_fisico(id_equipo: str, request: Request):
    INTENCIONES[f"historial_{id_equipo}"] = str(request.query_params)
    return CACHE_DATOS["historial_fisico"].get(id_equipo, [])

@app.get("/sincronizar/que_necesito")
def obtener_necesidades():
    """La planta descarga tus órdenes"""
    return INTENCIONES

@app.post("/update/{categoria}")
def actualizar_datos(categoria: str, datos: Any = Body(...)):
    """La planta sube los resultados"""
    global CACHE_DATOS
    if categoria in CACHE_DATOS:
        CACHE_DATOS[categoria] = datos
    elif "/" in categoria:
        tipo, id_obj = categoria.split("/")
        if tipo not in CACHE_DATOS: CACHE_DATOS[tipo] = {}
        CACHE_DATOS[tipo][id_obj] = datos
    return {"status": "ok"}
