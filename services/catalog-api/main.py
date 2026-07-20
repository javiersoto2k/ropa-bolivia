"""
services/catalog-api/main.py
Microservicio 3: Catálogo de productos — CRUD + búsqueda semántica + resumen IA
Puerto: 3003
"""
import os, sys, json, logging
sys.path.append("/app/shared")

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import anthropic

from models.database import (
    get_db, init_db, Producto, SessionLocal
)

log = logging.getLogger("catalog-api")
logging.basicConfig(level=logging.INFO)

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

app = FastAPI(title="Catalog API — RopaBolivia", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    try:
        init_db()
        log.info("Catalog API iniciada ✓")
    except Exception as e:
        log.error(f"DB no disponible al inicio: {e}")
        log.info("El servicio continúa sin DB — reintentará en cada request")

# ── SCHEMAS ───────────────────────────────────────────────────────────────────

class ProductoCreate(BaseModel):
    sku:                str
    nombre:             str
    descripcion:        Optional[str] = None
    categoria:          Optional[str] = None   # casual/deporte/fiesta/formal
    genero:             Optional[str] = None   # mujer/hombre/unisex
    tallas_disponibles: List[str] = []
    colores:            List[str] = []
    precio_costo:       float
    precio_venta:       float
    precio_promo:       Optional[float] = None
    stock:              dict = {}
    imagenes:           List[str] = []
    video_url:          Optional[str] = None
    destacado:          bool = False
    meta_tags:          List[str] = []

class ProductoUpdate(BaseModel):
    nombre:             Optional[str] = None
    precio_venta:       Optional[float] = None
    precio_promo:       Optional[float] = None
    stock:              Optional[dict] = None
    activo:             Optional[bool] = None
    destacado:          Optional[bool] = None

class StockUpdate(BaseModel):
    variante: str   # "S_negro"
    cantidad: int

# ── ENDPOINTS CRUD ────────────────────────────────────────────────────────────

@app.post("/productos", status_code=201)
def crear_producto(data: ProductoCreate, db: Session = Depends(get_db)):
    if db.query(Producto).filter(Producto.sku == data.sku).first():
        raise HTTPException(400, f"SKU {data.sku} ya existe")

    # Generar descripción IA automáticamente
    desc_ia = generar_descripcion_ia(data.nombre, data.categoria,
                                      data.genero, data.precio_venta)
    prod = Producto(**data.dict(), descripcion_ia=desc_ia)
    db.add(prod)
    db.commit()
    db.refresh(prod)
    log.info(f"Producto creado: {prod.sku}")
    return {"id": str(prod.id), "sku": prod.sku, "descripcion_ia": desc_ia}

@app.get("/productos")
def listar_productos(
    categoria: Optional[str] = None,
    genero:    Optional[str] = None,
    activo:    bool = True,
    destacado: Optional[bool] = None,
    limit:     int  = 50,
    offset:    int  = 0,
    db: Session = Depends(get_db)
):
    q = db.query(Producto).filter(Producto.activo == activo)
    if categoria: q = q.filter(Producto.categoria == categoria)
    if genero:    q = q.filter(Producto.genero    == genero)
    if destacado is not None: q = q.filter(Producto.destacado == destacado)
    total = q.count()
    prods = q.order_by(Producto.ventas_total.desc()).offset(offset).limit(limit).all()
    return {"total": total, "productos": [_serializar(p) for p in prods]}

@app.get("/productos/{sku}")
def obtener_producto(sku: str, db: Session = Depends(get_db)):
    prod = db.query(Producto).filter(Producto.sku == sku).first()
    if not prod:
        raise HTTPException(404, f"Producto {sku} no encontrado")
    return _serializar(prod)

@app.patch("/productos/{sku}")
def actualizar_producto(sku: str, data: ProductoUpdate, db: Session = Depends(get_db)):
    prod = db.query(Producto).filter(Producto.sku == sku).first()
    if not prod:
        raise HTTPException(404, "Producto no encontrado")
    for k, v in data.dict(exclude_none=True).items():
        setattr(prod, k, v)
    db.commit()
    return {"updated": sku}

@app.patch("/productos/{sku}/stock")
def actualizar_stock(sku: str, data: StockUpdate, db: Session = Depends(get_db)):
    prod = db.query(Producto).filter(Producto.sku == sku).first()
    if not prod:
        raise HTTPException(404, "Producto no encontrado")
    stock = dict(prod.stock or {})
    stock[data.variante] = max(0, stock.get(data.variante, 0) + data.cantidad)
    prod.stock = stock
    db.commit()
    return {"sku": sku, "stock": prod.stock}

@app.delete("/productos/{sku}")
def desactivar_producto(sku: str, db: Session = Depends(get_db)):
    prod = db.query(Producto).filter(Producto.sku == sku).first()
    if not prod:
        raise HTTPException(404, "Producto no encontrado")
    prod.activo = False
    db.commit()
    return {"desactivado": sku}

# ── BÚSQUEDA ──────────────────────────────────────────────────────────────────

@app.get("/productos/buscar/{query}")
def buscar_productos(query: str, db: Session = Depends(get_db)):
    """Búsqueda textual básica — en producción mejorar con pgvector."""
    termino = f"%{query.lower()}%"
    prods = db.query(Producto).filter(
        Producto.activo == True,
        (Producto.nombre.ilike(termino)) |
        (Producto.categoria.ilike(termino)) |
        (Producto.descripcion.ilike(termino))
    ).limit(10).all()
    return [_serializar(p) for p in prods]

# ── RESUMEN PARA IA (usado por whatsapp-bot y meta-webhook) ──────────────────

@app.get("/productos/resumen-ia", response_class=__import__("fastapi").responses.PlainTextResponse)
def resumen_ia(db: Session = Depends(get_db)):
    """Genera texto compacto del catálogo para insertar en system prompt de Claude."""
    prods = db.query(Producto).filter(
        Producto.activo == True
    ).order_by(Producto.ventas_total.desc()).limit(20).all()

    lineas = []
    for p in prods:
        precio = p.precio_promo or p.precio_venta
        stock_total = sum(p.stock.values()) if p.stock else 0
        if stock_total == 0:
            continue
        lineas.append(
            f"• {p.nombre} [{p.sku}] | {p.genero} | {p.categoria} | "
            f"Bs.{precio:.0f} | Tallas: {', '.join(p.tallas_disponibles)} | "
            f"Colores: {', '.join(p.colores)} | Stock: {stock_total}u"
        )

    return "\n".join(lineas) if lineas else "Catálogo en actualización."

# ── FEED XML PARA META COMMERCE MANAGER ──────────────────────────────────────

@app.get("/feed/meta.xml", response_class=__import__("fastapi").responses.Response)
def feed_meta(db: Session = Depends(get_db)):
    """Feed XML para Facebook/Instagram Shopping Catalog."""
    prods = db.query(Producto).filter(Producto.activo == True).all()
    items = []
    for p in prods:
        img = p.imagenes[0] if p.imagenes else ""
        precio = p.precio_promo or p.precio_venta
        stock_disp = "in stock" if sum((p.stock or {}).values()) > 0 else "out of stock"
        items.append(f"""    <item>
      <g:id>{p.sku}</g:id>
      <g:title>{p.nombre}</g:title>
      <g:description>{(p.descripcion_ia or p.descripcion or '')[:200]}</g:description>
      <g:link>https://ropabolivia.com/producto/{p.sku}</g:link>
      <g:image_link>{img}</g:image_link>
      <g:price>{precio:.2f} BOB</g:price>
      <g:availability>{stock_disp}</g:availability>
      <g:condition>new</g:condition>
      <g:brand>RopaBolivia</g:brand>
      <g:google_product_category>Apparel &amp; Accessories</g:google_product_category>
    </item>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">
  <channel>
    <title>RopaBolivia Catalog</title>
    <link>https://ropabolivia.com</link>
{chr(10).join(items)}
  </channel>
</rss>"""
    return __import__("fastapi").responses.Response(content=xml, media_type="application/xml")

# ── GENERACIÓN DE DESCRIPCIÓN CON IA ─────────────────────────────────────────

def generar_descripcion_ia(nombre: str, categoria: str, genero: str, precio: float) -> str:
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": f"""Genera una descripción atractiva para WhatsApp y redes sociales de este producto de ropa boliviana:
Producto: {nombre}
Categoría: {categoria}
Género: {genero}
Precio: Bs. {precio:.0f}

Máximo 3 oraciones. Lenguaje cercano, juvenil. Menciona el precio. Termina con emoji."""}]
        )
        return resp.content[0].text
    except Exception:
        return f"{nombre} — Bs. {precio:.0f}. Disponible en nuestro catálogo. 🛍️"

def _serializar(p: Producto) -> dict:
    return {
        "id":                  str(p.id),
        "sku":                 p.sku,
        "nombre":              p.nombre,
        "descripcion":         p.descripcion,
        "descripcion_ia":      p.descripcion_ia,
        "categoria":           p.categoria,
        "genero":              p.genero,
        "tallas_disponibles":  p.tallas_disponibles,
        "colores":             p.colores,
        "precio_costo":        p.precio_costo,
        "precio_venta":        p.precio_venta,
        "precio_promo":        p.precio_promo,
        "stock":               p.stock,
        "imagenes":            p.imagenes,
        "video_url":           p.video_url,
        "activo":              p.activo,
        "destacado":           p.destacado,
        "ventas_total":        p.ventas_total,
        "meta_tags":           p.meta_tags,
    }

@app.get("/health")
async def health():
    return {"status": "ok", "service": "catalog-api"}
