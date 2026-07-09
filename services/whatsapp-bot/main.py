"""
services/whatsapp-bot/main.py
Microservicio 1: Bot de WhatsApp con IA Claude — Atención 24/7
Deploy: EasyPanel → GitHub → Dockerfile
Puerto: 3001
"""
import os, json, logging, httpx, redis.asyncio as aioredis
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse
from contextlib import asynccontextmanager
import anthropic

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("whatsapp-bot")

# ── CONFIG ────────────────────────────────────────────────────────────────────
WA_TOKEN        = os.getenv("WA_ACCESS_TOKEN")
WA_PHONE_ID     = os.getenv("WA_PHONE_NUMBER_ID")
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "ropa_bolivia_2025")
ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY")
REDIS_URL       = os.getenv("REDIS_URL", "redis://localhost:6379")
CRM_API_URL     = os.getenv("CRM_API_URL", "http://crm-api:3004")
CATALOG_API_URL = os.getenv("CATALOG_API_URL", "http://catalog-api:3003")

SYSTEM_PROMPT = """Eres Valeria, asistente de ventas de RopaBolivia — boutique online de moda.
Tu rol: ayudar a los clientes a encontrar ropa, generar pedidos y cerrar ventas.

PERSONALIDAD: cálida, entusiasta, conocedora de moda boliviana. Usa emojis con moderación.
IDIOMA: español boliviano coloquial. Tutear siempre.
RESPUESTAS: máximo 3 oraciones. Directas al punto.

PAGOS ACEPTADOS:
- Tigo Money: 70123456 (a nombre de RopaBolivia)
- QR BNB: envío el código al confirmar pedido
- Transferencia: Banco Unión cta 123456789

ENVÍOS:
- La Paz / El Alto: Bs. 15 (1-2 días)
- Cbba / SCZ: Bs. 25 (2-3 días)
- Resto Bolivia: Bs. 30-45 (3-5 días)

PROCESO DE VENTA:
1. Detectar qué busca el cliente
2. Mostrar 2-3 opciones del catálogo
3. Confirmar talla, color y ciudad
4. Calcular total con envío
5. Dar instrucciones de pago
6. Pedir comprobante

ESCALAR A HUMANO si: queja fuerte, devolución, pago no confirmado en 2h, solicitud rara.
Para escalar, responde exactamente: [ESCALAR_HUMANO: motivo]

NUNCA inventes productos, precios ni disponibilidad. Usa solo info del catálogo proporcionado.
Si no tienes stock: ofrece lista de espera o producto similar."""

# ── STARTUP ───────────────────────────────────────────────────────────────────
redis_client: aioredis.Redis = None
claude_client: anthropic.AsyncAnthropic = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, claude_client
    redis_client  = aioredis.from_url(REDIS_URL, decode_responses=True)
    claude_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY)
    log.info("WhatsApp Bot iniciado ✓")
    yield
    await redis_client.aclose()

app = FastAPI(title="WhatsApp Bot — RopaBolivia", version="1.0.0", lifespan=lifespan)

# ── WEBHOOK VERIFICACIÓN ──────────────────────────────────────────────────────
@app.get("/webhook")
async def verificar_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_token == WA_VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    raise HTTPException(403, "Token inválido")

# ── WEBHOOK MENSAJES ENTRANTES ────────────────────────────────────────────────
@app.post("/webhook")
async def recibir_mensaje(request: Request):
    data = await request.json()
    try:
        entry    = data["entry"][0]
        changes  = entry["changes"][0]["value"]
        if "messages" not in changes:
            return {"status": "no_message"}

        msg      = changes["messages"][0]
        phone    = msg["from"]                          # 591XXXXXXXX
        nombre   = changes["contacts"][0]["profile"]["name"]
        msg_type = msg.get("type", "text")

        if msg_type == "text":
            texto = msg["text"]["body"]
        elif msg_type == "image":
            # Comprobante de pago — notificar a CRM
            await notificar_comprobante(phone, msg["image"]["id"])
            return {"status": "comprobante_recibido"}
        else:
            texto = "[mensaje no soportado]"

        log.info(f"Mensaje de {phone} ({nombre}): {texto[:60]}")

        # Cargar historial desde Redis
        historial = await cargar_historial(phone)

        # Cargar catálogo actualizado para contexto
        catalogo_ctx = await obtener_catalogo_resumen()

        # Generar respuesta con Claude
        respuesta = await generar_respuesta(phone, nombre, texto, historial, catalogo_ctx)

        # Manejar escalado a humano
        if "[ESCALAR_HUMANO:" in respuesta:
            motivo = respuesta.split("[ESCALAR_HUMANO:")[1].split("]")[0]
            await escalar_a_humano(phone, nombre, texto, motivo)
            respuesta = "Entiendo tu situación 🙏 Te conecto ahora con un asesor que te ayudará en minutos."

        # Guardar historial actualizado
        historial.append({"role": "user",      "content": texto})
        historial.append({"role": "assistant", "content": respuesta})
        await guardar_historial(phone, historial[-20:])   # últimos 20 mensajes

        # Registrar en CRM
        await registrar_en_crm(phone, nombre, texto, respuesta)

        # Enviar respuesta
        await enviar_mensaje_wa(phone, respuesta)

    except (KeyError, IndexError) as e:
        log.warning(f"Payload inesperado: {e}")

    return {"status": "ok"}

# ── IA: GENERAR RESPUESTA ─────────────────────────────────────────────────────
async def generar_respuesta(
    phone: str, nombre: str, mensaje: str,
    historial: list, catalogo_ctx: str
) -> str:
    system = f"{SYSTEM_PROMPT}\n\nCATÁLOGO ACTUAL:\n{catalogo_ctx}\nCLIENTE: {nombre} | Tel: {phone}"
    mensajes_api = historial[-10:] + [{"role": "user", "content": mensaje}]
    try:
        resp = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=system,
            messages=mensajes_api,
        )
        return resp.content[0].text
    except Exception as e:
        log.error(f"Error Claude: {e}")
        return "Un momento por favor, estamos teniendo una falla técnica. Te respondemos en segundos 🙏"

# ── ENVIAR MENSAJE WHATSAPP ───────────────────────────────────────────────────
async def enviar_mensaje_wa(phone: str, texto: str):
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": texto},
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=payload,
                              headers={"Authorization": f"Bearer {WA_TOKEN}"}, timeout=10)
        if r.status_code != 200:
            log.error(f"WA send error {r.status_code}: {r.text}")

async def enviar_template_wa(phone: str, template: str, params: list):
    """Envía mensajes template aprobados por Meta (para campañas)."""
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": "es_LA"},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": p} for p in params
            ]}],
        },
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload,
                          headers={"Authorization": f"Bearer {WA_TOKEN}"}, timeout=10)

# ── REDIS: HISTORIAL CONVERSACIONAL ──────────────────────────────────────────
async def cargar_historial(phone: str) -> list:
    raw = await redis_client.get(f"conv:{phone}")
    return json.loads(raw) if raw else []

async def guardar_historial(phone: str, historial: list):
    await redis_client.setex(f"conv:{phone}", 86400, json.dumps(historial))  # TTL 24h

# ── CRM: REGISTRAR INTERACCIÓN ────────────────────────────────────────────────
async def registrar_en_crm(phone: str, nombre: str, msg_in: str, msg_out: str):
    async with httpx.AsyncClient() as client:
        await client.post(f"{CRM_API_URL}/conversaciones/registrar", json={
            "whatsapp": phone, "nombre": nombre,
            "canal": "whatsapp", "mensaje_entrada": msg_in, "mensaje_salida": msg_out,
        }, timeout=5)

async def obtener_catalogo_resumen() -> str:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{CATALOG_API_URL}/productos/resumen-ia", timeout=5)
            return r.text if r.status_code == 200 else "Catálogo no disponible temporalmente."
    except Exception:
        return "Catálogo no disponible temporalmente."

async def notificar_comprobante(phone: str, image_id: str):
    async with httpx.AsyncClient() as client:
        await client.post(f"{CRM_API_URL}/pagos/comprobante", json={
            "whatsapp": phone, "image_id": image_id
        }, timeout=5)

async def escalar_a_humano(phone: str, nombre: str, msg: str, motivo: str):
    async with httpx.AsyncClient() as client:
        await client.post(f"{CRM_API_URL}/escalaciones/crear", json={
            "whatsapp": phone, "nombre": nombre,
            "ultimo_mensaje": msg, "motivo": motivo
        }, timeout=5)

# ── ENVÍO MASIVO (llamado por campaign-engine) ────────────────────────────────
@app.post("/broadcast")
async def broadcast(request: Request):
    data = await request.json()
    phones   = data.get("phones", [])
    template = data.get("template")
    params   = data.get("params", [])
    for phone in phones:
        await enviar_template_wa(phone, template, params)
    return {"enviados": len(phones)}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "whatsapp-bot"}
