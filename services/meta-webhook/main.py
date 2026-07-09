"""
services/meta-webhook/main.py
Microservicio 2: Webhooks Instagram DM + Facebook Messenger con IA
Puerto: 3002
"""
import os, json, logging, hashlib, hmac, httpx, redis.asyncio as aioredis
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse
from contextlib import asynccontextmanager
import anthropic

log = logging.getLogger("meta-webhook")
logging.basicConfig(level=logging.INFO)

META_APP_SECRET     = os.getenv("META_APP_SECRET")
META_PAGE_TOKEN     = os.getenv("META_PAGE_ACCESS_TOKEN")
META_VERIFY_TOKEN   = os.getenv("META_VERIFY_TOKEN", "ropa_bolivia_meta")
ANTHROPIC_KEY       = os.getenv("ANTHROPIC_API_KEY")
REDIS_URL           = os.getenv("REDIS_URL", "redis://localhost:6379")
CRM_API_URL         = os.getenv("CRM_API_URL", "http://crm-api:3004")
CATALOG_API_URL     = os.getenv("CATALOG_API_URL", "http://catalog-api:3003")

SYSTEM_META = """Eres Valeria, asistente de ventas de RopaBolivia en Instagram y Facebook.
Responde consultas sobre ropa, precios y pedidos. Máximo 2 oraciones por respuesta.
Siempre termina con una pregunta para mantener la conversación.
Para pedidos, pide al cliente que escriba al WhatsApp: +591 70123456.
Usa emojis relevantes. Tono juvenil y cercano."""

redis_client: aioredis.Redis = None
claude_client: anthropic.AsyncAnthropic = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, claude_client
    redis_client  = aioredis.from_url(REDIS_URL, decode_responses=True)
    claude_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY)
    log.info("Meta Webhook iniciado ✓")
    yield
    await redis_client.aclose()

app = FastAPI(title="Meta Webhook — RopaBolivia", version="1.0.0", lifespan=lifespan)

# ── VERIFICACIÓN WEBHOOK META ─────────────────────────────────────────────────
@app.get("/webhook")
async def verificar(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_token == META_VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    raise HTTPException(403, "Token inválido")

# ── WEBHOOK EVENTOS META ──────────────────────────────────────────────────────
@app.post("/webhook")
async def recibir_evento(request: Request):
    # Verificar firma
    firma    = request.headers.get("x-hub-signature-256", "")
    payload  = await request.body()
    expected = "sha256=" + hmac.new(META_APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(firma, expected):
        raise HTTPException(403, "Firma inválida")

    data = json.loads(payload)
    for entry in data.get("entry", []):
        # ── INSTAGRAM DM ──────────────────────────────────────────────────────
        for ig_msg in entry.get("messaging", []):
            sender_id = ig_msg["sender"]["id"]
            if "message" in ig_msg and "text" in ig_msg["message"]:
                texto = ig_msg["message"]["text"]
                await procesar_dm(sender_id, texto, "instagram")

        # ── FACEBOOK MESSENGER ────────────────────────────────────────────────
        for fb_msg in entry.get("messaging", []):
            sender_id = fb_msg["sender"]["id"]
            if "message" in fb_msg and "text" in fb_msg["message"]:
                texto = fb_msg["message"]["text"]
                await procesar_dm(sender_id, texto, "facebook")

        # ── COMENTARIOS EN POSTS ──────────────────────────────────────────────
        for change in entry.get("changes", []):
            if change.get("field") == "feed" and change["value"].get("item") == "comment":
                await procesar_comentario(change["value"])

    return {"status": "ok"}

async def procesar_dm(sender_id: str, texto: str, canal: str):
    historial = await cargar_historial(f"{canal}:{sender_id}")
    catalogo  = await obtener_catalogo()

    system = f"{SYSTEM_META}\nCATÁLOGO DISPONIBLE:\n{catalogo}"
    msgs   = historial[-8:] + [{"role": "user", "content": texto}]

    try:
        resp = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system,
            messages=msgs,
        )
        respuesta = resp.content[0].text
    except Exception:
        respuesta = "¡Hola! 👋 Para info sobre nuestros productos escríbenos al WhatsApp +591 70123456 🛍️"

    historial.append({"role": "user",      "content": texto})
    historial.append({"role": "assistant", "content": respuesta})
    await guardar_historial(f"{canal}:{sender_id}", historial[-16:])

    if canal == "instagram":
        await responder_ig_dm(sender_id, respuesta)
    else:
        await responder_fb_messenger(sender_id, respuesta)

    # Registrar en CRM
    async with httpx.AsyncClient() as client:
        await client.post(f"{CRM_API_URL}/conversaciones/registrar", json={
            "canal": canal, "session_id": sender_id,
            "mensaje_entrada": texto, "mensaje_salida": respuesta,
        }, timeout=5)

async def procesar_comentario(value: dict):
    """Responde automáticamente a comentarios en posts."""
    comentario = value.get("message", "")
    post_id    = value.get("post_id", "")
    comment_id = value.get("comment_id", "")

    palabras_compra = ["precio", "cuanto", "cuánto", "venden", "tienes", "tienen",
                       "donde", "dónde", "quiero", "comprar", "disponible"]
    if any(p in comentario.lower() for p in palabras_compra):
        respuesta = "¡Hola! 💕 Escríbenos al WhatsApp para atenderte personalizado 👉 +591 70123456 📱"
        await responder_comentario(comment_id, respuesta)

async def responder_ig_dm(user_id: str, texto: str):
    url = "https://graph.facebook.com/v19.0/me/messages"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "recipient": {"id": user_id},
            "message":   {"text": texto},
        }, headers={"Authorization": f"Bearer {META_PAGE_TOKEN}"}, timeout=10)

async def responder_fb_messenger(user_id: str, texto: str):
    url = "https://graph.facebook.com/v19.0/me/messages"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "recipient": {"id": user_id},
            "message":   {"text": texto},
            "messaging_type": "RESPONSE",
        }, headers={"Authorization": f"Bearer {META_PAGE_TOKEN}"}, timeout=10)

async def responder_comentario(comment_id: str, texto: str):
    url = f"https://graph.facebook.com/v19.0/{comment_id}/comments"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"message": texto},
                          headers={"Authorization": f"Bearer {META_PAGE_TOKEN}"}, timeout=10)

async def cargar_historial(key: str) -> list:
    raw = await redis_client.get(f"meta:{key}")
    return json.loads(raw) if raw else []

async def guardar_historial(key: str, historial: list):
    await redis_client.setex(f"meta:{key}", 86400, json.dumps(historial))

async def obtener_catalogo() -> str:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{CATALOG_API_URL}/productos/resumen-ia", timeout=4)
            return r.text if r.status_code == 200 else ""
    except Exception:
        return ""

@app.get("/health")
async def health():
    return {"status": "ok", "service": "meta-webhook"}
