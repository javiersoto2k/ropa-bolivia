"""
services/payment-gateway/main.py
Microservicio 10: Pasarela de pagos — Tigo Money, QR BNB, Transferencia
Puerto: 3005
"""
import os, sys, logging, hashlib, secrets
from datetime import datetime
sys.path.append("/app/shared")

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from models.database import get_db, init_db, Pago, Pedido, MetodoPago, PedidoEstado

log = logging.getLogger("payment-gateway")
logging.basicConfig(level=logging.INFO)

TIGO_API_KEY      = os.getenv("TIGO_MONEY_API_KEY")
TIGO_MERCHANT_ID  = os.getenv("TIGO_MONEY_MERCHANT_ID")
BNB_MERCHANT_CODE = os.getenv("BNB_MERCHANT_CODE")
CRM_API_URL       = os.getenv("CRM_API_URL", "http://crm-api:3004")

app = FastAPI(title="Payment Gateway — RopaBolivia", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    try:
        init_db()
        log.info("Payment Gateway iniciado ✓")
    except Exception as e:
        log.error(f"DB no disponible al inicio: {e}")
        log.info("El servicio continúa sin DB — reintentará en cada request")

# ═════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ═════════════════════════════════════════════════════════════════════════════

class PagoCreate(BaseModel):
    pedido_id:  str
    metodo:     str          # tigo_money | qr_bnb | transferencia | efectivo
    monto:      float
    referencia: Optional[str] = None

class VerificarPago(BaseModel):
    pedido_id:   str
    referencia:  str
    captura_url: Optional[str] = None

# ═════════════════════════════════════════════════════════════════════════════
# INSTRUCCIONES DE PAGO (para enviar al cliente por WhatsApp)
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/instrucciones/{pedido_id}")
def instrucciones_pago(pedido_id: str, db: Session = Depends(get_db)):
    """Genera instrucciones de pago personalizadas para un pedido."""
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(404, "Pedido no encontrado")

    codigo = pedido.numero
    total  = pedido.total

    instrucciones = {
        "numero_pedido": codigo,
        "total_bs":      total,
        "metodos": {
            "tigo_money": {
                "numero":    "70123456",
                "nombre":    "RopaBolivia SRL",
                "monto":     total,
                "mensaje":   f"Pago pedido {codigo}",
                "instruccion": (
                    f"📱 *Pago Tigo Money*\n"
                    f"Número: 70123456\n"
                    f"Nombre: RopaBolivia\n"
                    f"Monto: Bs. {total:.2f}\n"
                    f"Referencia: {codigo}\n\n"
                    f"Luego envíanos captura del comprobante ✅"
                ),
            },
            "qr_bnb": {
                "qr_url":     f"https://ropabolivia.com/qr/{codigo}",
                "instruccion": (
                    f"🔲 *Pago QR BNB*\n"
                    f"Escanea el QR que te enviamos\n"
                    f"Monto exacto: Bs. {total:.2f}\n"
                    f"Referencia: {codigo}\n\n"
                    f"El QR expira en 30 minutos ⏰"
                ),
            },
            "transferencia": {
                "banco":     "Banco Unión",
                "cuenta":    "1-6123456",
                "titular":   "RopaBolivia Comercial SRL",
                "ci":        "12345678",
                "monto":     total,
                "instruccion": (
                    f"🏦 *Transferencia Bancaria*\n"
                    f"Banco: Banco Unión\n"
                    f"Cuenta: 1-6123456\n"
                    f"Titular: RopaBolivia Comercial SRL\n"
                    f"Monto: Bs. {total:.2f}\n"
                    f"Concepto: {codigo}\n\n"
                    f"Envía el comprobante por WhatsApp 📎"
                ),
            },
        },
        "mensaje_whatsapp": (
            f"💳 *Datos de pago — Pedido {codigo}*\n\n"
            f"Total a pagar: *Bs. {total:.2f}*\n\n"
            f"Elige tu método:\n"
            f"1️⃣ Tigo Money: 70123456\n"
            f"2️⃣ QR BNB: te lo envío\n"
            f"3️⃣ Banco Unión: 1-6123456\n\n"
            f"¿Cuál prefieres? Responde 1, 2 o 3 🙏"
        ),
    }
    return instrucciones

# ═════════════════════════════════════════════════════════════════════════════
# REGISTRAR PAGO
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/pagos", status_code=201)
def registrar_pago(data: PagoCreate, db: Session = Depends(get_db)):
    pedido = db.query(Pedido).filter(Pedido.id == data.pedido_id).first()
    if not pedido:
        raise HTTPException(404, "Pedido no encontrado")

    pago = Pago(
        pedido_id  = pedido.id,
        metodo     = MetodoPago(data.metodo),
        monto      = data.monto,
        referencia = data.referencia,
        verificado = False,
    )
    db.add(pago)
    db.commit()
    db.refresh(pago)
    log.info(f"Pago registrado: {pago.id} | Pedido {pedido.numero} | Bs.{data.monto}")
    return {"pago_id": str(pago.id), "estado": "pendiente_verificacion"}

# ═════════════════════════════════════════════════════════════════════════════
# VERIFICAR PAGO (manual o automático por Tigo webhook)
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/pagos/verificar")
def verificar_pago(data: VerificarPago, db: Session = Depends(get_db)):
    pedido = db.query(Pedido).filter(Pedido.id == data.pedido_id).first()
    if not pedido:
        raise HTTPException(404, "Pedido no encontrado")

    pago = db.query(Pago).filter(Pago.pedido_id == pedido.id).first()
    if pago:
        pago.verificado    = True
        pago.verificado_por= "humano"
        pago.referencia    = data.referencia
        pago.captura_url   = data.captura_url

    pedido.estado      = PedidoEstado.pagado
    pedido.fecha_pago  = datetime.utcnow()
    pedido.referencia_pago = data.referencia
    db.commit()
    log.info(f"Pago verificado: Pedido {pedido.numero}")
    return {"verificado": True, "pedido": pedido.numero}

# ═════════════════════════════════════════════════════════════════════════════
# WEBHOOK TIGO MONEY (notificación automática de pago)
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/webhook/tigo")
async def webhook_tigo(request_data: dict, db: Session = Depends(get_db)):
    """
    Tigo Money notifica aquí cuando se recibe un pago.
    Formato del payload varía según contrato con Tigo Bolivia.
    """
    referencia = request_data.get("referencia") or request_data.get("reference")
    monto      = request_data.get("monto")      or request_data.get("amount")
    estado     = request_data.get("estado")     or request_data.get("status")

    log.info(f"[TIGO WEBHOOK] ref={referencia} monto={monto} estado={estado}")

    if estado in ["COMPLETED", "SUCCESS", "EXITOSO"]:
        # Buscar pedido por referencia (número de pedido en concepto)
        pedido = db.query(Pedido).filter(
            Pedido.numero == referencia,
            Pedido.estado == PedidoEstado.pendiente
        ).first()
        if pedido:
            pedido.estado     = PedidoEstado.pagado
            pedido.fecha_pago = datetime.utcnow()
            pedido.referencia_pago = referencia
            pago = Pago(
                pedido_id   = pedido.id,
                metodo      = MetodoPago.tigo_money,
                monto       = float(monto or 0),
                referencia  = referencia,
                verificado  = True,
                verificado_por = "tigo_api",
            )
            db.add(pago)
            db.commit()
            log.info(f"Pago Tigo confirmado automáticamente: {pedido.numero}")

    return {"received": True}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "payment-gateway"}
