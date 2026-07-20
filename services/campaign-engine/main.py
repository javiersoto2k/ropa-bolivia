"""
services/campaign-engine/main.py
Microservicio 8: Motor de campañas — orquesta envíos masivos y secuencias
Puerto: 3008
"""
import os, sys, logging, asyncio, httpx
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
sys.path.append("/app/shared")

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List

from models.database import get_db, init_db, Campana, Cliente, CanalVenta

log = logging.getLogger("campaign-engine")
logging.basicConfig(level=logging.INFO)

WA_BOT_URL  = os.getenv("WA_BOT_URL",      "http://whatsapp-bot:3001")
CRM_URL     = os.getenv("CRM_API_URL",      "http://crm-api:3004")
AI_URL      = os.getenv("AI_RECOMMENDER_URL","http://ai-recommender:3006")

app = FastAPI(title="Campaign Engine — RopaBolivia", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    try:
        init_db()
        log.info("Campaign Engine iniciado ✓")
    except Exception as e:
        log.error(f"DB no disponible al inicio: {e}")
        log.info("El servicio continúa sin DB — reintentará en cada request")

# ═════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ═════════════════════════════════════════════════════════════════════════════

class CampanaCreate(BaseModel):
    nombre:           str
    tipo:             str              # broadcast | flash_sale | reactivacion | vip | secuencia
    canal:            str              # whatsapp | instagram | facebook
    segmento:         dict = {}        # filtros: estado, ciudad, ltv_min, ltv_max, canal_origen
    mensaje_template: str
    imagen_url:       Optional[str] = None
    descuento_pct:    Optional[float] = None
    fecha_inicio:     Optional[str]  = None   # ISO format
    fecha_fin:        Optional[str]  = None

class SecuenciaCreate(BaseModel):
    nombre:     str
    trigger:    str              # carrito_abandonado | nuevo_cliente | postventa
    pasos: List[dict]            # [{espera_horas: 3, mensaje: "...", descuento_pct: 10}]

# ═════════════════════════════════════════════════════════════════════════════
# CAMPAÑAS CRUD
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/campanas", status_code=201)
def crear_campana(data: CampanaCreate, db: Session = Depends(get_db)):
    campana = Campana(
        nombre           = data.nombre,
        tipo             = data.tipo,
        canal            = CanalVenta(data.canal),
        segmento         = data.segmento,
        mensaje_template = data.mensaje_template,
        imagen_url       = data.imagen_url,
        descuento_pct    = data.descuento_pct,
        estado           = "borrador",
        fecha_inicio     = datetime.fromisoformat(data.fecha_inicio) if data.fecha_inicio else None,
        fecha_fin        = datetime.fromisoformat(data.fecha_fin)    if data.fecha_fin    else None,
    )
    db.add(campana)
    db.commit()
    db.refresh(campana)
    return {"id": str(campana.id), "nombre": campana.nombre, "estado": campana.estado}

@app.post("/campanas/{campana_id}/activar")
async def activar_campana(campana_id: str, db: Session = Depends(get_db)):
    campana = db.query(Campana).filter(Campana.id == campana_id).first()
    if not campana:
        raise HTTPException(404, "Campaña no encontrada")
    campana.estado = "activa"
    db.commit()
    # Ejecutar en background
    asyncio.create_task(ejecutar_campana(campana_id, db))
    return {"activada": campana.nombre}

@app.get("/campanas")
def listar_campanas(db: Session = Depends(get_db)):
    campanas = db.query(Campana).order_by(Campana.fecha_creacion.desc()).limit(50).all()
    return [
        {
            "id":              str(c.id),
            "nombre":          c.nombre,
            "tipo":            c.tipo,
            "estado":          c.estado,
            "total_enviados":  c.total_enviados,
            "total_convertidos": c.total_convertidos,
            "ingresos_bs":     c.ingresos_generados,
        }
        for c in campanas
    ]

# ═════════════════════════════════════════════════════════════════════════════
# EJECUCIÓN DE CAMPAÑA
# ═════════════════════════════════════════════════════════════════════════════

async def ejecutar_campana(campana_id: str, db: Session):
    """Motor de envío: filtra clientes por segmento y envía mensajes."""
    campana = db.query(Campana).filter(Campana.id == campana_id).first()
    if not campana:
        return

    log.info(f"[CAMPAÑA] Ejecutando: {campana.nombre}")

    # Filtrar clientes por segmento
    clientes = filtrar_clientes(db, campana.segmento)
    log.info(f"  → Segmento: {len(clientes)} clientes")

    enviados = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for c in clientes:
            if not c.whatsapp:
                continue
            nombre  = c.nombre or "amig@"
            mensaje = campana.mensaje_template.replace("{nombre}", nombre)
            if campana.descuento_pct:
                mensaje = mensaje.replace("{descuento}", str(int(campana.descuento_pct)))

            try:
                await client.post(f"{WA_BOT_URL}/broadcast", json={
                    "phones":   [c.whatsapp],
                    "template": campana.tipo,
                    "params":   [nombre, mensaje],
                })
                enviados += 1
                await asyncio.sleep(0.5)   # Rate limit: 2 mensajes/seg
            except Exception as e:
                log.warning(f"Error enviando a {c.whatsapp}: {e}")

    campana.total_enviados = enviados
    campana.estado         = "completada"
    db.commit()
    log.info(f"[CAMPAÑA] Completada: {campana.nombre} — {enviados} enviados")

def filtrar_clientes(db: Session, segmento: dict) -> list:
    q = db.query(Cliente)
    if segmento.get("estado"):
        q = q.filter(Cliente.estado == segmento["estado"])
    if segmento.get("ciudad"):
        q = q.filter(Cliente.ciudad.ilike(f"%{segmento['ciudad']}%"))
    if segmento.get("canal_origen"):
        q = q.filter(Cliente.canal_origen == segmento["canal_origen"])
    if segmento.get("ltv_min"):
        q = q.filter(Cliente.ltv_total >= segmento["ltv_min"])
    if segmento.get("ltv_max"):
        q = q.filter(Cliente.ltv_total <= segmento["ltv_max"])
    if segmento.get("min_pedidos"):
        q = q.filter(Cliente.num_pedidos >= segmento["min_pedidos"])
    return q.filter(Cliente.estado != "bloqueado").limit(500).all()

# ═════════════════════════════════════════════════════════════════════════════
# SECUENCIAS AUTOMÁTICAS (carrito abandonado, bienvenida, post-venta)
# ═════════════════════════════════════════════════════════════════════════════

SECUENCIAS = {
    "carrito_abandonado": [
        {"espera_horas": 3,  "descuento_pct": 0,
         "mensaje": "Hola {nombre} 👋 Vi que te interesó nuestra ropa. ¿Aún quieres ver el catálogo? Te ayudo a encontrar tu talla perfecta 💕"},
        {"espera_horas": 24, "descuento_pct": 10,
         "mensaje": "Hola {nombre} ✨ Todavía tenemos disponible lo que viste. Hoy te doy 10% OFF si confirmas ahora. ¡Solo hoy! ⚡"},
        {"espera_horas": 72, "descuento_pct": 15,
         "mensaje": "Última oportunidad {nombre} 🔥 Stock muy limitado + 15% descuento exclusivo. ¿Lo tomamos? 🛍️"},
    ],
    "nuevo_cliente": [
        {"espera_horas": 0,  "descuento_pct": 0,
         "mensaje": "¡Bienvenid@ {nombre} a RopaBolivia! 🎉 Somos tu boutique online de confianza. Escríbenos cuando quieras, estamos 24/7 💕"},
        {"espera_horas": 48, "descuento_pct": 0,
         "mensaje": "Hola {nombre} 👗 ¿Viste nuestro catálogo completo? Tenemos nueva llegada de ropa casual y deportiva. ¿Te muestro?"},
        {"espera_horas": 120,"descuento_pct": 10,
         "mensaje": "Hola {nombre} 🎁 Cupón especial de bienvenida: 10% en tu primera compra. Código: PRIMERA10. ¡Úsalo esta semana!"},
    ],
    "postventa": [
        {"espera_horas": 48, "descuento_pct": 0,
         "mensaje": "Hola {nombre} 😊 ¿Ya recibiste tu pedido? ¿Cómo te quedó la ropa? Nos encantaría saber 💬"},
        {"espera_horas": 168,"descuento_pct": 5,
         "mensaje": "Hola {nombre} ⭐ Si tu pedido llegó bien, ¿puedes dejarnos una reseña en Instagram? Te regalamos 5% en tu próxima compra 🙏"},
    ],
}

@app.post("/secuencias/iniciar")
async def iniciar_secuencia(data: dict, db: Session = Depends(get_db)):
    """Inicia una secuencia automática para un cliente."""
    tipo        = data.get("tipo")       # carrito_abandonado | nuevo_cliente | postventa
    cliente_id  = data.get("cliente_id")
    if tipo not in SECUENCIAS:
        raise HTTPException(400, f"Secuencia {tipo} no existe")

    pasos = SECUENCIAS[tipo]
    for paso in pasos:
        espera    = timedelta(hours=paso["espera_horas"])
        programado= datetime.utcnow() + espera
        async with httpx.AsyncClient() as client:
            await client.post(f"{CRM_URL}/seguimientos", json={
                "cliente_id":      cliente_id,
                "tipo":            tipo,
                "canal":           "whatsapp",
                "mensaje":         paso["mensaje"],
                "intento_num":     pasos.index(paso) + 1,
                "fecha_programada":programado.isoformat(),
                "datos_extra":     {"descuento_pct": paso.get("descuento_pct", 0)},
            })
    return {"secuencia": tipo, "pasos_programados": len(pasos)}

@app.get("/secuencias")
def listar_secuencias():
    return {tipo: len(pasos) for tipo, pasos in SECUENCIAS.items()}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "campaign-engine"}
