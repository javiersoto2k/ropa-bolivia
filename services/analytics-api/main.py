"""
services/analytics-api/main.py
Microservicio 7: Dashboard de gerencia — KPIs en tiempo real
Puerto: 3009
"""
import os, sys, logging
from datetime import datetime, timedelta
sys.path.append("/app/shared")

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, cast, Date

from models.database import (
    get_db, init_db, Cliente, Pedido, ItemPedido, Producto,
    Conversacion, PedidoEstado, CanalVenta, ClienteEstado
)

log = logging.getLogger("analytics-api")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Analytics API — RopaBolivia", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    try:
        init_db()
        log.info("Analytics API iniciada ✓")
    except Exception as e:
        log.error(f"DB no disponible al inicio: {e}")
        log.info("El servicio continúa sin DB — reintentará en cada request")

# ═════════════════════════════════════════════════════════════════════════════
# RESUMEN HOY (para reporte diario del worker)
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/resumen/hoy")
def resumen_hoy(db: Session = Depends(get_db)):
    hoy_inicio = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    pedidos_hoy = db.query(Pedido).filter(
        Pedido.fecha_pedido >= hoy_inicio,
        Pedido.estado.in_([PedidoEstado.pagado, PedidoEstado.enviado, PedidoEstado.entregado])
    ).all()

    ingresos = sum(p.total for p in pedidos_hoy)
    ticket_p = ingresos / len(pedidos_hoy) if pedidos_hoy else 0

    clientes_nuevos = db.query(Cliente).filter(
        Cliente.fecha_registro >= hoy_inicio
    ).count()

    convs_hoy = db.query(Conversacion).filter(
        Conversacion.fecha_inicio >= hoy_inicio
    ).count()

    convs_convertidas = db.query(Conversacion).filter(
        Conversacion.fecha_inicio >= hoy_inicio,
        Conversacion.convertido == True
    ).count()

    tasa = (convs_convertidas / convs_hoy * 100) if convs_hoy > 0 else 0

    return {
        "fecha":              datetime.utcnow().strftime("%Y-%m-%d"),
        "pedidos_hoy":        len(pedidos_hoy),
        "ingresos_hoy":       round(ingresos, 2),
        "ticket_promedio":    round(ticket_p, 2),
        "clientes_nuevos":    clientes_nuevos,
        "conversaciones_hoy": convs_hoy,
        "conversiones_hoy":   convs_convertidas,
        "tasa_conversion":    round(tasa, 2),
    }

# ═════════════════════════════════════════════════════════════════════════════
# KPIs PERÍODO (para dashboard gerencia)
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/kpis")
def kpis_periodo(dias: int = 30, db: Session = Depends(get_db)):
    desde = datetime.utcnow() - timedelta(days=dias)

    # Ventas totales
    pedidos = db.query(Pedido).filter(
        Pedido.fecha_pedido >= desde,
        Pedido.estado.in_([PedidoEstado.pagado, PedidoEstado.enviado, PedidoEstado.entregado])
    ).all()
    ingresos_total = sum(p.total for p in pedidos)
    ticket_prom    = ingresos_total / len(pedidos) if pedidos else 0

    # Ventas por canal
    ventas_canal = {}
    for canal in CanalVenta:
        total_canal = sum(p.total for p in pedidos if p.canal == canal)
        if total_canal > 0:
            ventas_canal[canal.value] = round(total_canal, 2)

    # Clientes nuevos vs recurrentes
    clientes_nuevos     = db.query(Cliente).filter(Cliente.fecha_registro >= desde).count()
    clientes_recurrentes = db.query(Cliente).filter(
        Cliente.num_pedidos > 1,
        Cliente.ultima_compra >= desde
    ).count()

    # Productos más vendidos
    top_productos = db.query(
        Producto.nombre, Producto.sku,
        func.sum(ItemPedido.cantidad).label("total_vendido"),
        func.sum(ItemPedido.subtotal).label("ingresos"),
    ).join(ItemPedido, ItemPedido.producto_id == Producto.id
    ).join(Pedido, Pedido.id == ItemPedido.pedido_id
    ).filter(
        Pedido.fecha_pedido >= desde,
        Pedido.estado.in_([PedidoEstado.pagado, PedidoEstado.enviado, PedidoEstado.entregado])
    ).group_by(Producto.id, Producto.nombre, Producto.sku
    ).order_by(func.sum(ItemPedido.cantidad).desc()
    ).limit(10).all()

    # Conversaciones y tasa
    total_convs    = db.query(Conversacion).filter(Conversacion.fecha_inicio >= desde).count()
    convs_convert  = db.query(Conversacion).filter(
        Conversacion.fecha_inicio >= desde, Conversacion.convertido == True
    ).count()
    tasa_conv = (convs_convert / total_convs * 100) if total_convs > 0 else 0

    # Segmentación de clientes
    segs = {}
    for est in ClienteEstado:
        segs[est.value] = db.query(Cliente).filter(Cliente.estado == est).count()

    return {
        "periodo_dias":       dias,
        "desde":              desde.strftime("%Y-%m-%d"),
        "hasta":              datetime.utcnow().strftime("%Y-%m-%d"),
        "ventas": {
            "total_pedidos":   len(pedidos),
            "ingresos_total":  round(ingresos_total, 2),
            "ticket_promedio": round(ticket_prom, 2),
            "por_canal":       ventas_canal,
        },
        "clientes": {
            "nuevos":          clientes_nuevos,
            "recurrentes":     clientes_recurrentes,
            "segmentacion":    segs,
        },
        "conversiones": {
            "total_chats":     total_convs,
            "convertidos":     convs_convert,
            "tasa_pct":        round(tasa_conv, 2),
        },
        "top_productos": [
            {
                "nombre":       p.nombre,
                "sku":          p.sku,
                "unidades":     int(p.total_vendido),
                "ingresos_bs":  round(float(p.ingresos), 2),
            }
            for p in top_productos
        ],
    }

# ═════════════════════════════════════════════════════════════════════════════
# VENTAS POR DÍA (para gráfico de línea)
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/ventas/por-dia")
def ventas_por_dia(dias: int = 30, db: Session = Depends(get_db)):
    desde = datetime.utcnow() - timedelta(days=dias)
    rows  = db.query(
        cast(Pedido.fecha_pedido, Date).label("dia"),
        func.count(Pedido.id).label("pedidos"),
        func.sum(Pedido.total).label("ingresos"),
    ).filter(
        Pedido.fecha_pedido >= desde,
        Pedido.estado.in_([PedidoEstado.pagado, PedidoEstado.enviado, PedidoEstado.entregado])
    ).group_by(cast(Pedido.fecha_pedido, Date)
    ).order_by(cast(Pedido.fecha_pedido, Date)).all()

    return [
        {"dia": str(r.dia), "pedidos": r.pedidos, "ingresos": round(float(r.ingresos or 0), 2)}
        for r in rows
    ]

# ═════════════════════════════════════════════════════════════════════════════
# ROI PUBLICITARIO (datos manuales o vía Meta API)
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/roi/publicitario")
def roi_publicitario(db: Session = Depends(get_db)):
    """
    En producción: conectar con Meta Ads API para gasto real.
    Por ahora retorna estructura esperada con datos de ejemplo.
    """
    return {
        "meta_ads": {
            "gasto_mes_bs":    800,
            "ingresos_attr_bs": 2800,
            "roas":             3.5,
            "cpc_bs":           2.1,
            "cpm_bs":           45,
        },
        "tiktok_organico": {
            "views_mes":       85000,
            "clics_wa":        1200,
            "ventas_attr":     68,
            "ingresos_bs":     12400,
        },
        "total": {
            "inversion_bs":    800,
            "ingresos_bs":     15200,
            "roi_pct":         1800,
        }
    }

# ═════════════════════════════════════════════════════════════════════════════
# INVENTARIO CRÍTICO
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/inventario/critico")
def inventario_critico(umbral: int = 5, db: Session = Depends(get_db)):
    """Productos con stock total menor al umbral."""
    prods = db.query(Producto).filter(Producto.activo == True).all()
    criticos = []
    for p in prods:
        stock_total = sum((p.stock or {}).values())
        if stock_total <= umbral:
            criticos.append({
                "sku":         p.sku,
                "nombre":      p.nombre,
                "stock_total": stock_total,
                "stock_detalle": p.stock,
                "ventas_total":  p.ventas_total,
            })
    criticos.sort(key=lambda x: x["stock_total"])
    return {"umbral": umbral, "total_criticos": len(criticos), "productos": criticos}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "analytics-api"}
