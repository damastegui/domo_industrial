from fastapi import FastAPI, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Any, Dict, List
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALMACEN = {
    "dashboard": [],
    "equipos": [],
    "sensores": {},
    "analisis": {},
    "eventos": {},
    "historial_fisico": {}
}

@app.get("/")
def raiz():
    return {"estado": "Buzón Full Activo"}

@app.post("/update/{categoria}")
def actualizar_datos(categoria: str, datos: Any = Body(...)):
    global ALMACEN
    if categoria in ALMACEN:
        ALMACEN[categoria] = datos
        return {"status": "ok"}
    
    if "/" in categoria:
        partes = categoria.split("/")
        tipo = partes[0]
        id_obj = partes[1]
        
        if tipo not in ALMACEN: ALMACEN[tipo] = {}
        ALMACEN[tipo][id_obj] = datos
        
    return {"status": "recibido"}

@app.get("/dashboard/resumen")
def get_dashboard():
    return ALMACEN["dashboard"]

@app.get("/equipos")
def get_equipos():
    return ALMACEN["equipos"]

@app.get("/sensores/{id_equipo}")
def get_sensores(id_equipo: str):
    return ALMACEN["sensores"].get(id_equipo, [])

@app.get("/analisis/{id_equipo}")
def get_analisis(id_equipo: str):
    return ALMACEN["analisis"].get(id_equipo, {"datos": []})

@app.get("/eventos/{id_equipo}")
def get_eventos(id_equipo: str):
    return ALMACEN["eventos"].get(id_equipo, [])

@app.get("/historial_fisico_raw/{id_equipo}")
def get_historial_fisico(id_equipo: str, inicio: str = Query(...), fin: str = Query(...)):
    datos_completos = ALMACEN["historial_fisico"].get(id_equipo, [])
    
    if not datos_completos:
        return []

    try:
        dt_inicio = datetime.fromisoformat(inicio.replace('Z', '+00:00'))
        dt_fin = datetime.fromisoformat(fin.replace('Z', '+00:00'))
        
        datos_filtrados = []
        for punto in datos_completos:
            fecha_str = punto.get('hora') or punto.get('tiempo') or punto.get('bucket')
            if fecha_str:
                dt_punto = datetime.fromisoformat(fecha_str.replace('Z', '+00:00'))
                if dt_inicio <= dt_punto <= dt_fin:
                    datos_filtrados.append(punto)
                    
        return datos_filtrados
    except Exception:
        return datos_completos
