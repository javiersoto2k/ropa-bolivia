"""
services/crm-api/main.py
Microservicio 4: CRM Automático — Clientes, Pedidos, Conversaciones, Segmentación
Puerto: 3004
"""
import os, sys, uuid, logging
from datetime import datetime, timedelta
sys.path.append("/app/shared")

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from pydantic import BaseModel
from typing import Optional, List

from models.database import (
    get_db, init_db, Cliente, Pedido, ItemPedido, Pago,
    Conversacion, Seguimiento, Campana,
    ClienteEstado, PedidoEstado, CanalVenta, MetodoPago
)
from utils.helpers import (
    generar_numero_pedido, limpiar_telefono,
    calcular_envio, segmentar_cliente, dias_desde
)

log = logging.getLogger("crm-api")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="CRM API — RopaBolivia", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    init_db()
    log.info("CRM API iniciada ✓")

# ═════════════════════════════════════════════════════════════════════════════
# CLIENTES
# ═════════════════════════════════════════════════════════════════════════════

class ClienteUpsert(BaseModel):
    whatsapp:       Optional[str] = None
    instagram_id:   Optional[str] = None
    facebook_id:    Optional[str] = None
    nombre:         Optional[str] = None
    ciudad:         Optional[str] = None
    talla_mujer:    Optional[str] = None
    talla_hombre:   Optional[str] = None
    estilo:         Optional[str] = None
    canal_origen:   Optional[str] = None
    tags:           List[str] = []

@app.post("/clientes/upsert")
def upsert_cliente(data: ClienteUpsert, db: Session = Depends(get_db)):
    """Crea o actualiza cliente por WhatsApp / Instagram / Facebook ID."""
    cliente = None
    if data.whatsapp:
        telefono = limpiar_telefono(data.whatsapp)
        cliente  = db.query(Cliente).filter(Cliente.whatsapp == telefono).first()
        if not cliente:
            cliente = Cliente(whatsapp=telefono)
            db.add(cliente)
    elif data.instagram_id:
        cliente = db.query(Cliente).filter(Cliente.instagram_id == data.instagram_id).first()
        if not cliente:
            cliente = Cliente(instagram_id=data.instagram_id)
            db.add(cliente)
    elif data.facebook_id:
        cliente = db.query(Cliente).filter(Cliente.facebook_id == data.facebook_id).first()
        if not cliente:
            cliente = Cliente(facebook_id=data.facebook_id)
            db.add(cliente)

    if not cliente:
        raise HTTPException(400, "Se requiere whatsapp, instagram_id o facebook_id")

    # Actualizar campos no nulos
    for campo in ["nombre", "ciudad", "talla_mujer", "talla_hombre", "estilo"]:
        val = getattr(data, campo)
        if val:
            setattr(cliente, campo, val)
    if data.canal_origen and not cliente.canal_origen:
        cliente.canal_origen = data.canal_origen
    if data.tags:
        tags_actuales = set(cliente.tags or [])
        cliente.tags  = list(tags_actuales | set(data.tags))
    cliente.ultima_interaccion = datetime.utcnow()

    # Auto-segmentar
    dias_inact = dias_desde(cliente.ultima_compra)
    nuevo_estado = segmentar_cliente(cliente.ltv_total, cliente.num_pedidos, dias_inact)
    cliente.estado = ClienteEstado(nuevo_estado)

    db.commit()
    db.refresh(cliente)
    return {"id": str(cliente.id), "estado": cliente.estado, "es_nuevo": cliente.num_pedidos == 0}

@app.get("/clientes/{cliente_id}")
def obtener_cliente(cliente_id: str, db: Session = Depends(get_db)):
    c = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not c:
        raise HTTPException(404, "Cliente no encontrado")
    return _serializar_cliente(c)

@app.get("/clientes")
def listar_clientes(
    estado:  Optional[str] = None,
    ciudad:  Optional[str] = None,
    canal:   Optional[str] = None,
    limit:   int = 100,
    offset:  int = 0,
    db: Session = Depends(get_db)
):
    q = db.query(Cliente)
    if estado: q = q.filter(Cliente.estado == estado)
    if ciudad: q = q.filter(Cliente.ciudad.ilike(f"%{ciudad}%"))
    if canal:  q = q.filter(Cliente.canal_origen == canal)
    total = q.count()
    clientes = q.order_by(Cliente.ultima_interaccion.desc()).offset(offset).limit(limit).all()
    return {"total": total, "clientes": [_serializar_cliente(c) for c in clientes]}

@app.get("/clientes/segmento/inactivos")
def clientes_inactivos(dias: int = 30, db: Session = Depends(get_db)):
    """Clientes sin compra hace N días (para campañas de reactivación)."""
    corte = datetime.utcnow() - timedelta(days=dias)
    cs = db.query(Cliente).filter(
        or_(Cliente.ultima_compra < corte, Cliente.ultima_compra == None),
        Cliente.num_pedidos >= 1,
        Cliente.estado != ClienteEstado.bloqueado
    ).all()
    return {"total": len(cs), "clientes": [_mini_cliente(c) for c in cs]}

@app.get("/clientes/segmento/vip")
def clientes_vip(db: Session = Depends(get_db)):
    cs = db.query(Cliente).filter(Cliente.estado == ClienteEstado.vip).all()
    return {"total": len(cs), "clientes": [_mini_cliente(c) for c in cs]}

# ═════════════════════════════════════════════════════════════════════════════
# PEDIDOS
# ═════════════════════════════════════════════════════════════════════════════

class ItemCreate(BaseModel):
    producto_sku:   str
    producto_id:    str
    talla:          Optional[str] = None
    color:          Optional[str] = None
    cantidad:       int = 1
    precio_unitario: float

class PedidoCreate(BaseModel):
    cliente_id:      str
    canal:           str
    items:           List[ItemCreate]
    ciudad_entrega:  Optional[str] = None
    direccion:       Optional[str] = None
    descuento:       float = 0.0
    notas:           Optional[str] = None

@app.post("/pedidos", status_code=201)
def crear_pedido(data: PedidoCreate, db: Session = Depends(get_db)):
    subtotal = sum(i.precio_unitario * i.cantidad for i in data.items)
    envio    = calcular_envio(data.ciudad_entrega or "")
    total    = subtotal + envio - data.descuento

    pedido = Pedido(
        numero           = generar_numero_pedido(),
        cliente_id       = data.cliente_id,
        canal            = CanalVenta(data.canal),
        estado           = PedidoEstado.pendiente,
        subtotal         = subtotal,
        costo_envio      = envio,
        descuento        = data.descuento,
        total            = total,
        ciudad_entrega   = data.ciudad_entrega,
        direccion_entrega= data.direccion,
    )
    db.add(pedido)
    db.flush()

    for i in data.items:
        item = ItemPedido(
            pedido_id       = pedido.id,
            producto_id     = i.producto_id,
            talla           = i.talla,
            color           = i.color,
            cantidad        = i.cantidad,
            precio_unitario = i.precio_unitario,
            subtotal        = i.precio_unitario * i.cantidad,
        )
        db.add(item)

    db.commit()
    db.refresh(pedido)
    log.info(f"Pedido creado: {pedido.numero} | Total: Bs.{total:.2f}")
    return {
        "id": str(pedido.id), "numero": pedido.numero,
        "subtotal": subtotal, "envio": envio, "total": total,
    }

@app.patch("/pedidos/{pedido_id}/estado")
def actualizar_estado_pedido(pedido_id: str, estado: str, db: Session = Depends(get_db)):
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(404, "Pedido no encontrado")
    pedido.estado = PedidoEstado(estado)
    if estado == "pagado":    pedido.fecha_pago    = datetime.utcnow()
    if estado == "enviado":   pedido.fecha_envio   = datetime.utcnow()
    if estado == "entregado":
        pedido.fecha_entrega = datetime.utcnow()
        # Actualizar LTV del cliente
        c = db.query(Cliente).filter(Cliente.id == pedido.cliente_id).first()
        if c:
            c.ltv_total    += pedido.total
            c.num_pedidos  += 1
            c.ultima_compra = datetime.utcnow()
            c.estado        = ClienteEstado.vip if c.ltv_total >= 500 else ClienteEstado.activo
    db.commit()
    return {"pedido": pedido.numero, "nuevo_estado": estado}

@app.get("/pedidos/{pedido_id}")
def obtener_pedido(pedido_id: str, db: Session = Depends(get_db)):
    p = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not p:
        raise HTTPException(404, "Pedido no encontrado")
    return {"numero": p.numero, "estado": p.estado, "total": p.total,
            "items": len(p.items), "fecha": str(p.fecha_pedido)}

# ═════════════════════════════════════════════════════════════════════════════
# CONVERSACIONES (para bots)
# ═════════════════════════════════════════════════════════════════════════════

class ConversacionRegistrar(BaseModel):
    canal:           str
    session_id:      Optional[str] = None
    whatsapp:        Optional[str] = None
    nombre:          Optional[str] = None
    mensaje_entrada: str
    mensaje_salida:  str

@app.post("/conversaciones/registrar")
def registrar_conversacion(data: ConversacionRegistrar, db: Session = Depends(get_db)):
    # Upsert cliente si viene por WhatsApp
    cliente_id = None
    if data.whatsapp:
        tel = limpiar_telefono(data.whatsapp)
        c   = db.query(Cliente).filter(Cliente.whatsapp == tel).first()
        if not c:
            c = Cliente(whatsapp=tel, nombre=data.nombre,
                        canal_origen=CanalVenta(data.canal))
            db.add(c)
            db.flush()
        c.ultima_interaccion = datetime.utcnow()
        cliente_id = c.id

    session_id = data.whatsapp or data.session_id or "unknown"
    conv = db.query(Conversacion).filter(
        Conversacion.session_id == session_id,
        Conversacion.canal      == CanalVenta(data.canal)
    ).first()

    nuevo_mensaje = [
        {"role": "user",      "content": data.mensaje_entrada, "ts": datetime.utcnow().isoformat()},
        {"role": "assistant", "content": data.mensaje_salida,  "ts": datetime.utcnow().isoformat()},
    ]

    if conv:
        msgs = list(conv.mensajes or [])
        msgs.extend(nuevo_mensaje)
        conv.mensajes     = msgs[-40:]
        conv.fecha_ultimo = datetime.utcnow()
    else:
        conv = Conversacion(
            cliente_id = cliente_id,
            canal      = CanalVenta(data.canal),
            session_id = session_id,
            mensajes   = nuevo_mensaje,
        )
        db.add(conv)

    db.commit()
    return {"ok": True}

# ═════════════════════════════════════════════════════════════════════════════
# PAGOS / COMPROBANTES
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/pagos/comprobante")
def recibir_comprobante(data: dict, db: Session = Depends(get_db)):
    """Registra llegada de comprobante de pago (imagen WhatsApp)."""
    whatsapp = limpiar_telefono(data.get("whatsapp", ""))
    c = db.query(Cliente).filter(Cliente.whatsapp == whatsapp).first()
    log.info(f"Comprobante recibido de {whatsapp} — image_id: {data.get('image_id')}")
    # En producción: descargar imagen de Meta API y guardar en R2
    return {"ok": True, "pendiente_verificacion": True}

# ═════════════════════════════════════════════════════════════════════════════
# ESCALACIONES
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/escalaciones/crear")
def crear_escalacion(data: dict, db: Session = Depends(get_db)):
    """Registra escalación a humano y notifica vía canal interno."""
    log.warning(f"[ESCALACIÓN] {data.get('whatsapp')} — {data.get('motivo')}")
    # En producción: enviar alerta al dueño por Telegram/WhatsApp
    return {"ok": True}

# ═════════════════════════════════════════════════════════════════════════════
# SEGUIMIENTOS (carrito abandonado, post-venta)
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/seguimientos")
def crear_seguimiento(data: dict, db: Session = Depends(get_db)):
    seg = Seguimiento(
        cliente_id       = data["cliente_id"],
        tipo             = data["tipo"],
        canal            = CanalVenta(data.get("canal", "whatsapp")),
        mensaje          = data.get("mensaje"),
        intento_num      = data.get("intento_num", 1),
        fecha_programada = datetime.fromisoformat(data["fecha_programada"]),
        datos_extra      = data.get("datos_extra", {}),
    )
    db.add(seg)
    db.commit()
    return {"id": str(seg.id)}

@app.get("/seguimientos/pendientes")
def seguimientos_pendientes(db: Session = Depends(get_db)):
    """Llamado por notification-worker cada 5 minutos."""
    ahora = datetime.utcnow()
    segs  = db.query(Seguimiento).filter(
        Seguimiento.estado           == "pendiente",
        Seguimiento.fecha_programada <= ahora,
    ).limit(50).all()
    return [
        {
            "id":         str(s.id),
            "cliente_id": str(s.cliente_id),
            "tipo":       s.tipo,
            "canal":      s.canal,
            "mensaje":    s.mensaje,
            "intento":    s.intento_num,
            "extra":      s.datos_extra,
        }
        for s in segs
    ]

@app.patch("/seguimientos/{seg_id}/enviado")
def marcar_enviado(seg_id: str, convertido: bool = False, db: Session = Depends(get_db)):
    s = db.query(Seguimiento).filter(Seguimiento.id == seg_id).first()
    if s:
        s.estado      = "enviado"
        s.fecha_enviado = datetime.utcnow()
        s.convertido  = convertido
        db.commit()
    return {"ok": True}

# ─────────────────────────────────────────────────────────────────────────────

def _serializar_cliente(c: Cliente) -> dict:
    return {
        "id":               str(c.id),
        "nombre":           c.nombre,
        "whatsapp":         c.whatsapp,
        "ciudad":           c.ciudad,
        "estado":           c.estado,
        "ltv_total":        c.ltv_total,
        "num_pedidos":      c.num_pedidos,
        "canal_origen":     c.canal_origen,
        "talla_mujer":      c.talla_mujer,
        "talla_hombre":     c.talla_hombre,
        "estilo":           c.estilo,
        "tags":             c.tags,
        "ultima_compra":    str(c.ultima_compra) if c.ultima_compra else None,
        "fecha_registro":   str(c.fecha_registro),
    }

def _mini_cliente(c: Cliente) -> dict:
    return {"id": str(c.id), "nombre": c.nombre, "whatsapp": c.whatsapp,
            "ciudad": c.ciudad, "estado": c.estado, "ltv": c.ltv_total}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "crm-api"}
