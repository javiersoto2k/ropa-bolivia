"""
services/notification-worker/main.py
Microservicio 6: Worker de notificaciones — Carritos abandonados, seguimientos,
                  campañas automáticas, reactivación. Reemplaza n8n completamente.
Puerto: 3010
"""
import os, sys, asyncio, logging, httpx
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
sys.path.append("/app/shared")

from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval  import IntervalTrigger
from apscheduler.triggers.cron      import CronTrigger

log = logging.getLogger("notification-worker")
logging.basicConfig(level=logging.INFO)

WA_BOT_URL      = os.getenv("WA_BOT_URL",      "http://whatsapp-bot:3001")
CRM_URL         = os.getenv("CRM_API_URL",      "http://crm-api:3004")
AI_URL          = os.getenv("AI_RECOMMENDER_URL","http://ai-recommender:3006")
CAMPAIGN_URL    = os.getenv("CAMPAIGN_ENGINE_URL","http://campaign-engine:3008")
ANALYTICS_URL   = os.getenv("ANALYTICS_API_URL","http://analytics-api:3009")

scheduler = AsyncIOScheduler()

# ═════════════════════════════════════════════════════════════════════════════
# JOB 1: Seguimientos pendientes (cada 5 min)
# ─ Envía mensajes de carrito abandonado, post-venta, etc.
# ═════════════════════════════════════════════════════════════════════════════

async def job_seguimientos():
    log.info("[JOB] Procesando seguimientos pendientes...")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{CRM_URL}/seguimientos/pendientes")
        if r.status_code != 200:
            return
        segs = r.json()
        log.info(f"  → {len(segs)} seguimientos a enviar")

        for seg in segs:
            whatsapp = await obtener_whatsapp_cliente(client, seg["cliente_id"])
            if not whatsapp:
                continue

            nombre = await obtener_nombre_cliente(client, seg["cliente_id"])
            mensaje = (seg.get("mensaje") or "").replace("{nombre}", nombre or "")

            # Enviar por WhatsApp
            await client.post(f"{WA_BOT_URL}/broadcast", json={
                "phones":   [whatsapp],
                "template": "seguimiento_general",
                "params":   [nombre or "amig@", mensaje],
            })

            # Marcar como enviado en CRM
            await client.patch(f"{CRM_URL}/seguimientos/{seg['id']}/enviado")
            log.info(f"  → Seguimiento enviado a {whatsapp} ({seg['tipo']})")

# ═════════════════════════════════════════════════════════════════════════════
# JOB 2: Carritos abandonados (cada 3 horas)
# ─ Detecta clientes que consultaron pero no compraron
# ═════════════════════════════════════════════════════════════════════════════

async def job_carritos_abandonados():
    log.info("[JOB] Verificando carritos abandonados...")
    async with httpx.AsyncClient(timeout=20) as client:
        # Obtener copy de IA para carrito abandonado
        r = await client.post(f"{AI_URL}/contenido/campaign-copy", json={
            "tipo_campana":  "carrito_abandonado",
            "segmento":      "activo",
            "descuento_pct": 10,
        })
        if r.status_code != 200:
            return
        versiones = r.json().get("versiones", [])
        if not versiones:
            return
        mensaje_base = versiones[0]

        # Obtener clientes con consultas sin pedido en últimas 3-6h
        # (En producción: query de conversaciones sin conversión)
        r2 = await client.get(f"{CRM_URL}/clientes/segmento/inactivos?dias=1")
        if r2.status_code != 200:
            return
        clientes = r2.json().get("clientes", [])

        for c in clientes[:20]:   # máx 20 por ejecución
            if not c.get("whatsapp"):
                continue
            nombre  = c.get("nombre") or "amig@"
            mensaje = mensaje_base.replace("{nombre}", nombre)

            await client.post(f"{WA_BOT_URL}/broadcast", json={
                "phones":   [c["whatsapp"]],
                "template": "carrito_abandonado",
                "params":   [nombre, "tu prenda favorita", "10"],
            })
            log.info(f"  → Carrito abandonado enviado a {c['whatsapp']}")

# ═════════════════════════════════════════════════════════════════════════════
# JOB 3: Reactivación de clientes inactivos (lunes 10:00 AM)
# ═════════════════════════════════════════════════════════════════════════════

async def job_reactivacion():
    log.info("[JOB] Campaña de reactivación semanal...")
    async with httpx.AsyncClient(timeout=30) as client:
        # Clientes inactivos >30 días
        r = await client.get(f"{CRM_URL}/clientes/segmento/inactivos?dias=30")
        if r.status_code != 200:
            return
        inactivos = r.json().get("clientes", [])

        # Copy IA para reactivación
        r2 = await client.post(f"{AI_URL}/contenido/campaign-copy", json={
            "tipo_campana":  "reactivacion",
            "segmento":      "inactivo",
            "descuento_pct": 15,
        })
        versiones = r2.json().get("versiones", []) if r2.status_code == 200 else []
        mensaje_base = versiones[0] if versiones else "¡Hola {nombre}! Te extrañamos 💕 Tenemos novedades para ti. ¡Escríbenos!"

        phones_validos = [c["whatsapp"] for c in inactivos if c.get("whatsapp")]
        log.info(f"  → Enviando reactivación a {len(phones_validos)} clientes")

        for phone in phones_validos[:100]:  # máx 100 por ciclo
            nombre  = next((c["nombre"] for c in inactivos if c["whatsapp"] == phone), "amig@") or "amig@"
            mensaje = mensaje_base.replace("{nombre}", nombre)
            await client.post(f"{WA_BOT_URL}/broadcast", json={
                "phones":   [phone],
                "template": "reactivacion_cliente",
                "params":   [nombre, "15"],
            })

# ═════════════════════════════════════════════════════════════════════════════
# JOB 4: Campaña VIP — preventas (viernes 18:00)
# ═════════════════════════════════════════════════════════════════════════════

async def job_campana_vip():
    log.info("[JOB] Campaña VIP — preventa viernes...")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{CRM_URL}/clientes/segmento/vip")
        if r.status_code != 200:
            return
        vips = r.json().get("clientes", [])

        r2 = await client.post(f"{AI_URL}/contenido/campaign-copy", json={
            "tipo_campana": "vip",
            "segmento":     "vip",
        })
        versiones   = r2.json().get("versiones", []) if r2.status_code == 200 else []
        msg_base    = versiones[0] if versiones else "¡Hola {nombre}! 👑 Preventa exclusiva VIP — 24h antes que todos. ¿Qué talla necesitas?"

        phones = [c["whatsapp"] for c in vips if c.get("whatsapp")]
        log.info(f"  → Campaña VIP a {len(phones)} clientes")
        for phone in phones:
            nombre = next((c["nombre"] for c in vips if c["whatsapp"] == phone), "VIP") or "VIP"
            await client.post(f"{WA_BOT_URL}/broadcast", json={
                "phones":   [phone],
                "template": "preventa_vip",
                "params":   [nombre],
            })

# ═════════════════════════════════════════════════════════════════════════════
# JOB 5: Flash sale automático (viernes 20:00)
# ═════════════════════════════════════════════════════════════════════════════

async def job_flash_sale():
    log.info("[JOB] Iniciando flash sale viernes...")
    async with httpx.AsyncClient(timeout=20) as client:
        r_copy = await client.post(f"{AI_URL}/contenido/campaign-copy", json={
            "tipo_campana":  "flash_sale",
            "segmento":      "activo",
            "descuento_pct": 20,
        })
        versiones  = r_copy.json().get("versiones", []) if r_copy.status_code == 200 else []
        msg_base   = versiones[0] if versiones else "⚡ FLASH SALE 4 horas — 20% OFF en todo. Solo hoy viernes hasta medianoche. Escríbenos YA 👇"

        # Todos los clientes activos
        r_cli = await client.get(f"{CRM_URL}/clientes?estado=activo&limit=500")
        if r_cli.status_code != 200:
            return
        clientes = r_cli.json().get("clientes", [])
        phones   = [c["whatsapp"] for c in clientes if c.get("whatsapp")]
        log.info(f"  → Flash sale a {len(phones)} clientes")

        for phone in phones[:200]:
            nombre = next((c["nombre"] for c in clientes if c["whatsapp"] == phone), "amig@") or "amig@"
            await client.post(f"{WA_BOT_URL}/broadcast", json={
                "phones":   [phone],
                "template": "flash_sale",
                "params":   [nombre, "20"],
            })

# ═════════════════════════════════════════════════════════════════════════════
# JOB 6: Post-venta automático (cada hora)
# ─ Detecta pedidos entregados hace 48h y pide reseña
# ═════════════════════════════════════════════════════════════════════════════

async def job_postventa():
    log.info("[JOB] Seguimiento post-venta...")
    # En producción: query pedidos entregados hace 48h sin seguimiento enviado
    # Aquí lógica simplificada
    pass

# ═════════════════════════════════════════════════════════════════════════════
# JOB 7: Reporte diario al dueño (09:00 AM)
# ═════════════════════════════════════════════════════════════════════════════

async def job_reporte_diario():
    log.info("[JOB] Generando reporte diario...")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{ANALYTICS_URL}/resumen/hoy")
        if r.status_code == 200:
            resumen = r.json()
            mensaje = (
                f"📊 *Reporte RopaBolivia — {datetime.now().strftime('%d/%m/%Y')}*\n\n"
                f"💰 Ventas: Bs. {resumen.get('ingresos_hoy', 0):,.0f}\n"
                f"🛒 Pedidos: {resumen.get('pedidos_hoy', 0)}\n"
                f"👥 Clientes nuevos: {resumen.get('clientes_nuevos', 0)}\n"
                f"📱 WhatsApp: {resumen.get('conversaciones_hoy', 0)} chats\n"
                f"🎯 Conversión: {resumen.get('tasa_conversion', 0):.1f}%\n"
                f"⭐ Ticket promedio: Bs. {resumen.get('ticket_promedio', 0):,.0f}"
            )
            # Enviar al dueño (número configurado)
            dueno_phone = os.getenv("OWNER_WHATSAPP", "59170000000")
            await client.post(f"{WA_BOT_URL}/broadcast", json={
                "phones":   [dueno_phone],
                "template": "reporte_diario",
                "params":   [mensaje],
            })

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def obtener_whatsapp_cliente(client: httpx.AsyncClient, cliente_id: str) -> str:
    r = await client.get(f"{CRM_URL}/clientes/{cliente_id}", timeout=5)
    return r.json().get("whatsapp") if r.status_code == 200 else None

async def obtener_nombre_cliente(client: httpx.AsyncClient, cliente_id: str) -> str:
    r = await client.get(f"{CRM_URL}/clientes/{cliente_id}", timeout=5)
    return r.json().get("nombre") if r.status_code == 200 else "amig@"

# ═════════════════════════════════════════════════════════════════════════════
# ARRANQUE CON APSCHEDULER
# ═════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seguimientos: cada 5 minutos
    scheduler.add_job(job_seguimientos,      IntervalTrigger(minutes=5),  id="seguimientos")
    # Carritos abandonados: cada 3 horas
    scheduler.add_job(job_carritos_abandonados, IntervalTrigger(hours=3), id="carritos")
    # Reactivación: lunes 10:00 AM
    scheduler.add_job(job_reactivacion,      CronTrigger(day_of_week="mon", hour=10, minute=0), id="reactivacion")
    # VIP preventa: viernes 18:00
    scheduler.add_job(job_campana_vip,       CronTrigger(day_of_week="fri", hour=18, minute=0), id="vip")
    # Flash sale: viernes 20:00
    scheduler.add_job(job_flash_sale,        CronTrigger(day_of_week="fri", hour=20, minute=0), id="flash_sale")
    # Post-venta: cada hora
    scheduler.add_job(job_postventa,         IntervalTrigger(hours=1),    id="postventa")
    # Reporte diario: 09:00 AM
    scheduler.add_job(job_reporte_diario,    CronTrigger(hour=9, minute=0), id="reporte")

    scheduler.start()
    log.info("Notification Worker iniciado ✓ — todos los jobs activos")
    yield
    scheduler.shutdown()

app = FastAPI(title="Notification Worker — RopaBolivia", version="1.0.0", lifespan=lifespan)

@app.get("/health")
async def health():
    jobs = [{"id": j.id, "next_run": str(j.next_run_time)} for j in scheduler.get_jobs()]
    return {"status": "ok", "service": "notification-worker", "jobs": jobs}

@app.post("/trigger/{job_id}")
async def trigger_manual(job_id: str):
    """Ejecuta un job manualmente desde el panel de administración."""
    jobs_map = {
        "seguimientos":  job_seguimientos,
        "carritos":      job_carritos_abandonados,
        "reactivacion":  job_reactivacion,
        "vip":           job_campana_vip,
        "flash_sale":    job_flash_sale,
        "reporte":       job_reporte_diario,
    }
    if job_id not in jobs_map:
        return {"error": f"Job {job_id} no existe"}
    asyncio.create_task(jobs_map[job_id]())
    return {"triggered": job_id}
