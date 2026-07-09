"""
services/ai-recommender/main.py
Microservicio 5: Motor de recomendaciones IA personalizado
Puerto: 3006
"""
import os, sys, logging, httpx
sys.path.append("/app/shared")

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import anthropic

log = logging.getLogger("ai-recommender")
logging.basicConfig(level=logging.INFO)

ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY")
CRM_API_URL     = os.getenv("CRM_API_URL", "http://crm-api:3004")
CATALOG_API_URL = os.getenv("CATALOG_API_URL", "http://catalog-api:3003")

claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
app    = FastAPI(title="AI Recommender — RopaBolivia", version="1.0.0")

class RecomendacionRequest(BaseModel):
    cliente_id:  Optional[str] = None
    whatsapp:    Optional[str] = None
    contexto:    Optional[str] = None   # "quiero algo para fiesta", "regalo mujer"
    presupuesto: Optional[float] = None
    genero:      Optional[str] = None
    ocasion:     Optional[str] = None

class ContentScriptRequest(BaseModel):
    red_social: str           # tiktok | instagram | facebook | youtube
    tipo:       str           # reel | story | post | short | live
    producto:   Optional[str] = None
    tema:       Optional[str] = None   # "outfit_trabajo", "flash_sale", "nueva_llegada"

class CampaignCopyRequest(BaseModel):
    tipo_campana:  str        # reactivacion | flash_sale | vip | carrito_abandonado
    segmento:      str        # vip | inactivo | nuevo | activo
    producto:      Optional[str] = None
    descuento_pct: Optional[float] = None

# ═════════════════════════════════════════════════════════════════════════════
# RECOMENDACIONES PERSONALIZADAS
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/recomendar")
async def recomendar_productos(data: RecomendacionRequest):
    """Genera recomendaciones personalizadas basadas en perfil + catálogo."""
    # Obtener catálogo
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{CATALOG_API_URL}/productos/resumen-ia", timeout=5)
        catalogo = r.text if r.status_code == 200 else ""

    # Obtener perfil de cliente si existe
    perfil = ""
    if data.cliente_id:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{CRM_API_URL}/clientes/{data.cliente_id}", timeout=5)
            if r.status_code == 200:
                c = r.json()
                perfil = (
                    f"Cliente: {c.get('nombre')} | Ciudad: {c.get('ciudad')} | "
                    f"Compras prev.: {c.get('num_pedidos')} | LTV: Bs.{c.get('ltv_total')} | "
                    f"Talla mujer: {c.get('talla_mujer')} | Talla hombre: {c.get('talla_hombre')} | "
                    f"Estilo favorito: {c.get('estilo')} | Estado: {c.get('estado')}"
                )

    contexto = data.contexto or "ropa en general"
    presupuesto = f"Bs. {data.presupuesto:.0f}" if data.presupuesto else "sin límite"

    prompt = f"""Eres experta en moda boliviana. Recomienda 3 productos del catálogo para este cliente.

PERFIL:
{perfil or 'Cliente nuevo, sin historial.'}

SOLICITUD: {contexto}
PRESUPUESTO: {presupuesto}
GÉNERO BUSCADO: {data.genero or 'cualquiera'}
OCASIÓN: {data.ocasion or 'no especificada'}

CATÁLOGO DISPONIBLE:
{catalogo}

FORMATO DE RESPUESTA (JSON):
{{
  "recomendaciones": [
    {{
      "sku": "SKU123",
      "nombre": "...",
      "precio": 0,
      "razon": "Por qué le queda perfecto a este cliente (1 oración)",
      "tip_estilo": "Cómo combinarlo (1 oración)"
    }}
  ],
  "mensaje_personalizado": "Mensaje para enviar al cliente por WhatsApp (máx. 3 oraciones, cálido)"
}}

Solo incluye productos que existan en el catálogo. Responde SOLO el JSON."""

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        log.error(f"Error recomendador: {e}")
        raise HTTPException(500, "Error generando recomendaciones")

# ═════════════════════════════════════════════════════════════════════════════
# GENERACIÓN DE CONTENIDO PARA REDES SOCIALES
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/contenido/generar-script")
async def generar_script(data: ContentScriptRequest):
    """Genera script/caption para publicación en red social."""

    guias = {
        "tiktok": "Hook en segundos 0-3, mostrar producto con emoción, CTA al WhatsApp. Máx 60 seg. Usa trending sounds bolivianos.",
        "instagram": "Caption con gancho, historia de 2-3 líneas, 5 hashtags Bolivia + moda. Stories con swipe-up a WhatsApp.",
        "facebook": "Texto más largo para audiencia 25-45 años. Precio visible, CTA directo. Emojis moderados.",
        "youtube": "Título SEO: 'ropa [categoría] Bolivia 2025'. Description con keywords. CTA en primeros 30 seg del Short.",
    }

    tipos_contenido = {
        "reel":  "Video 15-60 seg con música trending",
        "story": "Imagen/video vertical 9:16, máx 15 seg",
        "post":  "Imagen cuadrada o carrusel con caption",
        "short": "Video vertical YouTube Shorts, máx 60 seg",
        "live":  "Guión para live de ventas en vivo",
    }

    prompt = f"""Eres creador de contenido experto en moda boliviana para redes sociales.
Genera contenido viral para {data.red_social.upper()}.

TIPO: {tipos_contenido.get(data.tipo, data.tipo)}
PRODUCTO/TEMA: {data.producto or data.tema or 'nueva colección de ropa'}
GUÍA DE LA PLATAFORMA: {guias.get(data.red_social, '')}
NEGOCIO: RopaBolivia — boutique online, envíos a todo Bolivia, pago Tigo Money y QR
AUDIENCIA: Mujeres y hombres bolivianos 18-40 años

Genera:
1. HOOK/TÍTULO (primeras palabras/frase que engancha)
2. SCRIPT completo (lo que dice el vendedor o el caption)
3. HASHTAGS (10 relevantes Bolivia + moda)
4. CTA final (llamada a la acción)
5. TIPS de grabación/publicación

Formato: directo, lenguaje boliviano coloquial, mentalidad de venta."""

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        return {
            "red_social": data.red_social,
            "tipo":       data.tipo,
            "contenido":  resp.content[0].text,
        }
    except Exception as e:
        log.error(f"Error script: {e}")
        raise HTTPException(500, "Error generando script")

# ═════════════════════════════════════════════════════════════════════════════
# COPY PARA CAMPAÑAS (llamado por campaign-engine)
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/contenido/campaign-copy")
async def generar_campaign_copy(data: CampaignCopyRequest):
    """Genera texto personalizado para campañas de WhatsApp."""
    contextos = {
        "reactivacion":       "Cliente que no compra hace más de 30 días. Objetivo: hacerlo volver con oferta atractiva.",
        "flash_sale":         "Oferta relámpago por tiempo limitado (4-6 horas). Urgencia máxima.",
        "vip":                "Cliente VIP exclusivo. Trato especial, preventa antes que nadie.",
        "carrito_abandonado": "Mostró interés en un producto pero no completó la compra.",
    }
    segmentos = {
        "vip":      "Cliente fiel, más de 3 compras, trato exclusivo",
        "inactivo": "No compra hace 30+ días, necesita incentivo fuerte",
        "nuevo":    "Primera interacción, construir confianza",
        "activo":   "Compra regularmente, oferta de valor",
    }

    prompt = f"""Eres copywriter experto en ventas por WhatsApp para Bolivia.
Escribe 3 versiones de mensaje para campaña.

TIPO DE CAMPAÑA: {contextos.get(data.tipo_campana, data.tipo_campana)}
SEGMENTO: {segmentos.get(data.segmento, data.segmento)}
PRODUCTO: {data.producto or 'nueva colección'}
DESCUENTO: {f'{data.descuento_pct:.0f}%' if data.descuento_pct else 'sin descuento específico'}
NEGOCIO: RopaBolivia — moda online Bolivia

Para cada versión:
- Usar {{nombre}} como placeholder para personalización
- Máximo 3 oraciones
- Tono: cálido + urgente + honesto
- Incluir CTA claro (WhatsApp, pago, visitar catálogo)
- Usar emojis estratégicamente (máx 3)

Formato JSON:
{{"versiones": ["mensaje1", "mensaje2", "mensaje3"]}}"""

    try:
        import json
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"): text = text[4:]
        return json.loads(text)
    except Exception as e:
        log.error(f"Error campaign copy: {e}")
        raise HTTPException(500, "Error generando copy")

# ═════════════════════════════════════════════════════════════════════════════
# ANÁLISIS DE TENDENCIAS
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/tendencias/analizar")
async def analizar_tendencias(data: dict):
    """Analiza datos de ventas y sugiere productos a pedir."""
    ventas_data = data.get("ventas_ultimos_30_dias", {})
    prompt = f"""Analiza estos datos de ventas de ropa en Bolivia y recomienda acciones:

VENTAS ÚLTIMOS 30 DÍAS:
{ventas_data}

Proporciona:
1. Top 3 productos a reponer urgente
2. Productos a descontinuar (bajo stock + bajas ventas)
3. Tendencias detectadas (estilo, género, talla)
4. Recomendación de 2 productos nuevos a agregar al catálogo
5. Sugerencia de promoción para la próxima semana

Contexto: Bolivia, temporada actual, mercado online joven 18-40 años."""

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}]
    )
    return {"analisis": resp.content[0].text}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-recommender"}
