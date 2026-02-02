from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from typing import Any, Dict

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
    "eventos": {}
}

@app.get("/")
def raiz():
    return {"estado": "Buzón Activo"}

@app.post("/update/{categoria}")
def actualizar_datos(categoria: str, datos: Any = Body(...)):
    global ALMACEN
    if categoria in ALMACEN:
        ALMACEN[categoria] = datos
        return {"status": "ok"}

    if "/" in categoria:
        tipo, id_obj = categoria.split("/")
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
