"""
services/content-scheduler/main.py
Microservicio 9: Programador de contenido — TikTok, Instagram, Facebook, YouTube
Puerto: 3007
"""
import os, sys, logging, httpx
from datetime import datetime, timedelta
sys.path.append("/app/shared")

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List

from models.database import get_db, init_db, ContenidoProgramado

log = logging.getLogger("content-scheduler")
logging.basicConfig(level=logging.INFO)

META_PAGE_TOKEN  = os.getenv("META_PAGE_ACCESS_TOKEN")
META_PAGE_ID     = os.getenv("META_PAGE_ID")
META_IG_ACCOUNT  = os.getenv("META_IG_ACCOUNT_ID")
AI_URL           = os.getenv("AI_RECOMMENDER_URL", "http://ai-recommender:3006")

app = FastAPI(title="Content Scheduler — RopaBolivia", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    init_db()
    log.info("Content Scheduler iniciado ✓")

# ═════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ═════════════════════════════════════════════════════════════════════════════

class ContenidoCreate(BaseModel):
    red_social:       str              # instagram | facebook | youtube | tiktok
    tipo:             str              # reel | story | post | short
    caption:          Optional[str]  = None
    hashtags:         List[str]       = []
    media_url:        Optional[str]  = None
    fecha_programada: str              # ISO datetime
    generado_por_ia:  bool            = False

class GenerarCalendarioRequest(BaseModel):
    dias:             int              = 30
    frecuencia_diaria: int             = 3   # posts por día
    redes:            List[str]        = ["tiktok", "instagram", "facebook", "youtube"]

# ═════════════════════════════════════════════════════════════════════════════
# PROGRAMAR CONTENIDO
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/contenido", status_code=201)
def programar_contenido(data: ContenidoCreate, db: Session = Depends(get_db)):
    c = ContenidoProgramado(
        red_social       = data.red_social,
        tipo             = data.tipo,
        caption          = data.caption,
        hashtags         = data.hashtags,
        media_url        = data.media_url,
        estado           = "pendiente",
        fecha_programada = datetime.fromisoformat(data.fecha_programada),
        generado_por_ia  = data.generado_por_ia,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": str(c.id), "programado_para": data.fecha_programada}

@app.get("/contenido")
def listar_contenido(
    estado:     Optional[str] = None,
    red_social: Optional[str] = None,
    db: Session = Depends(get_db)
):
    q = db.query(ContenidoProgramado)
    if estado:     q = q.filter(ContenidoProgramado.estado     == estado)
    if red_social: q = q.filter(ContenidoProgramado.red_social == red_social)
    posts = q.order_by(ContenidoProgramado.fecha_programada).limit(100).all()
    return [
        {
            "id":               str(p.id),
            "red_social":       p.red_social,
            "tipo":             p.tipo,
            "caption":          (p.caption or "")[:100],
            "estado":           p.estado,
            "fecha_programada": str(p.fecha_programada),
            "fecha_publicado":  str(p.fecha_publicado) if p.fecha_publicado else None,
        }
        for p in posts
    ]

# ═════════════════════════════════════════════════════════════════════════════
# PUBLICAR EN INSTAGRAM/FACEBOOK (Meta Graph API)
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/publicar/instagram/{contenido_id}")
async def publicar_instagram(contenido_id: str, db: Session = Depends(get_db)):
    c = db.query(ContenidoProgramado).filter(ContenidoProgramado.id == contenido_id).first()
    if not c:
        raise HTTPException(404, "Contenido no encontrado")

    caption_completo = f"{c.caption}\n\n{' '.join('#'+h for h in (c.hashtags or []))}"

    async with httpx.AsyncClient(timeout=30) as client:
        # Paso 1: Crear container de media
        r1 = await client.post(
            f"https://graph.facebook.com/v19.0/{META_IG_ACCOUNT}/media",
            params={
                "image_url":  c.media_url,
                "caption":    caption_completo,
                "access_token": META_PAGE_TOKEN,
            }
        )
        if r1.status_code != 200:
            raise HTTPException(500, f"Error creando media: {r1.text}")

        container_id = r1.json().get("id")

        # Paso 2: Publicar
        r2 = await client.post(
            f"https://graph.facebook.com/v19.0/{META_IG_ACCOUNT}/media_publish",
            params={"creation_id": container_id, "access_token": META_PAGE_TOKEN}
        )
        if r2.status_code != 200:
            raise HTTPException(500, f"Error publicando: {r2.text}")

        post_id = r2.json().get("id")
        c.estado         = "publicado"
        c.fecha_publicado= datetime.utcnow()
        c.post_id_externo= post_id
        db.commit()
        return {"publicado": True, "post_id": post_id, "red": "instagram"}

@app.post("/publicar/facebook/{contenido_id}")
async def publicar_facebook(contenido_id: str, db: Session = Depends(get_db)):
    c = db.query(ContenidoProgramado).filter(ContenidoProgramado.id == contenido_id).first()
    if not c:
        raise HTTPException(404, "Contenido no encontrado")

    caption = f"{c.caption}\n\n{' '.join('#'+h for h in (c.hashtags or []))}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://graph.facebook.com/v19.0/{META_PAGE_ID}/photos",
            params={
                "url":          c.media_url,
                "message":      caption,
                "access_token": META_PAGE_TOKEN,
            }
        )
        if r.status_code != 200:
            raise HTTPException(500, f"Error FB: {r.text}")

        c.estado          = "publicado"
        c.fecha_publicado = datetime.utcnow()
        c.post_id_externo = r.json().get("id")
        db.commit()
        return {"publicado": True, "post_id": c.post_id_externo, "red": "facebook"}

# ═════════════════════════════════════════════════════════════════════════════
# GENERAR CALENDARIO 30 DÍAS CON IA
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/calendario/generar")
async def generar_calendario(data: GenerarCalendarioRequest, db: Session = Depends(get_db)):
    """Genera y guarda un calendario de contenido de 30 días con IA."""
    TEMAS_30_DIAS = [
        # Semana 1: Lanzamiento
        ("nueva_llegada",    "tiktok",    "reel"),
        ("try_on_haul",      "instagram", "reel"),
        ("outfit_trabajo",   "facebook",  "post"),
        ("tutorial_combinar","youtube",   "short"),
        ("testimonios",      "instagram", "story"),
        ("flash_sale",       "tiktok",    "reel"),
        ("live_ventas",      "instagram", "reel"),
        # Semana 2: Engagement
        ("outfit_check",     "tiktok",    "reel"),
        ("unboxing",         "instagram", "reel"),
        ("precio_vs_calidad","facebook",  "post"),
        ("tallas_guia",      "youtube",   "short"),
        ("behind_escenas",   "instagram", "story"),
        ("combo_look",       "tiktok",    "reel"),
        ("nueva_llegada",    "facebook",  "post"),
        # Semana 3: Conversión
        ("flash_sale",       "tiktok",    "reel"),
        ("testimonios",      "instagram", "reel"),
        ("outfit_fiesta",    "youtube",   "short"),
        ("moda_boliviana",   "facebook",  "post"),
        ("try_on_haul",      "tiktok",    "reel"),
        ("ugc_repost",       "instagram", "story"),
        ("live_ventas",      "tiktok",    "reel"),
        # Semana 4: Fidelización
        ("nueva_llegada",    "instagram", "reel"),
        ("outfit_deporte",   "tiktok",    "reel"),
        ("coleccion_mes",    "facebook",  "post"),
        ("tallas_guia",      "instagram", "story"),
        ("precio_especial",  "tiktok",    "reel"),
        ("testimonio_video", "youtube",   "short"),
        ("preventa_vip",     "facebook",  "post"),
        ("resumen_mes",      "instagram", "reel"),
        ("nueva_llegada",    "tiktok",    "reel"),
    ]

    HORARIOS = {
        "tiktok":    [9, 13, 20],
        "instagram": [8, 12, 19],
        "facebook":  [10, 15, 20],
        "youtube":   [11, 17],
    }

    programados = []
    hoy = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    for i, (tema, red, tipo) in enumerate(TEMAS_30_DIAS[:data.dias]):
        if red not in data.redes:
            continue
        dia      = hoy + timedelta(days=i)
        horarios = HORARIOS.get(red, [10])
        hora_pub = horarios[i % len(horarios)]
        fecha_pub= dia.replace(hour=hora_pub)

        # Generar script con IA
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{AI_URL}/contenido/generar-script", json={
                    "red_social": red,
                    "tipo":       tipo,
                    "tema":       tema,
                })
                contenido_ia = r.json().get("contenido", "") if r.status_code == 200 else ""
        except Exception:
            contenido_ia = f"Contenido sobre {tema} para {red}"

        # Caption = primeras 3 líneas del script IA
        caption = "\n".join(contenido_ia.split("\n")[:3]) if contenido_ia else tema

        c = ContenidoProgramado(
            red_social       = red,
            tipo             = tipo,
            caption          = caption[:500],
            hashtags         = _hashtags_por_tema(tema),
            estado           = "pendiente",
            fecha_programada = fecha_pub,
            generado_por_ia  = True,
        )
        db.add(c)
        programados.append({"dia": i+1, "red": red, "tipo": tipo, "tema": tema, "hora": hora_pub})

    db.commit()
    return {
        "calendario_generado": True,
        "posts_programados":   len(programados),
        "dias":                data.dias,
        "resumen":             programados,
    }

def _hashtags_por_tema(tema: str) -> list:
    base = ["#RopaBolivia", "#ModaBolivia", "#RopaOnlineBolivia", "#LaPaz", "#Bolivia"]
    extra = {
        "flash_sale":    ["#OfertaBolivia", "#Descuento", "#FlashSale"],
        "try_on_haul":   ["#OOTD", "#TryOn", "#HaulBolivia"],
        "nueva_llegada": ["#NuevaColeccion", "#NuevaLlegada", "#TrendBolivia"],
        "outfit_trabajo":["#OutfitTrabajo", "#RopaOficina", "#LookProfesional"],
        "outfit_fiesta": ["#OutfitFiesta", "#ModaNoche", "#LookFiesta"],
        "moda_boliviana":["#ModaBoliviana", "#EstiloBoliviano", "#Moda2025"],
        "testimonios":   ["#ClientesFelices", "#Reviews", "#Testimonio"],
    }
    return base + extra.get(tema, ["#Moda", "#Style", "#Fashion"])

@app.get("/health")
async def health():
    return {"status": "ok", "service": "content-scheduler"}
