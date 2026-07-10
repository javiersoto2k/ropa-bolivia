"""
shared/models/database.py
Modelos SQLAlchemy compartidos entre todos los microservicios.
"""
import uuid, enum, os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Integer, Float, Boolean,
    DateTime, JSON, Text, Enum, ForeignKey, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/ropa_bolivia")
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ── ENUMS ────────────────────────────────────────────────────────────────────

class ClienteEstado(str, enum.Enum):
    nuevo = "nuevo"; activo = "activo"; inactivo = "inactivo"
    vip = "vip";     bloqueado = "bloqueado"

class PedidoEstado(str, enum.Enum):
    pendiente = "pendiente"; pagado = "pagado"; preparando = "preparando"
    enviado = "enviado";     entregado = "entregado"; cancelado = "cancelado"

class CanalVenta(str, enum.Enum):
    whatsapp = "whatsapp"; instagram = "instagram"; facebook = "facebook"
    tiktok   = "tiktok";   youtube   = "youtube";   directo  = "directo"

class MetodoPago(str, enum.Enum):
    tigo_money   = "tigo_money"
    qr_bnb       = "qr_bnb"
    transferencia= "transferencia"
    efectivo     = "efectivo"

# ── CLIENTES ─────────────────────────────────────────────────────────────────

class Cliente(Base):
    __tablename__ = "clientes"
    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    whatsapp            = Column(String(20),  unique=True, nullable=True, index=True)
    instagram_id        = Column(String(50),  unique=True, nullable=True)
    facebook_id         = Column(String(50),  unique=True, nullable=True)
    nombre              = Column(String(150), nullable=True)
    ciudad              = Column(String(80),  nullable=True)
    talla_mujer         = Column(String(5),   nullable=True)
    talla_hombre        = Column(String(5),   nullable=True)
    estilo              = Column(String(50),  nullable=True)   # casual/formal/deporte
    canal_origen        = Column(Enum(CanalVenta), nullable=True)
    estado              = Column(Enum(ClienteEstado), default=ClienteEstado.nuevo)
    ltv_total           = Column(Float,   default=0.0)
    num_pedidos         = Column(Integer, default=0)
    ultima_compra       = Column(DateTime, nullable=True)
    ultima_interaccion  = Column(DateTime, default=datetime.utcnow)
    fecha_registro      = Column(DateTime, default=datetime.utcnow)
    tags                = Column(JSON, default=list)
    datos_extra         = Column(JSON, default=dict)
    pedidos             = relationship("Pedido",      back_populates="cliente")
    conversaciones      = relationship("Conversacion",back_populates="cliente")
    seguimientos        = relationship("Seguimiento", back_populates="cliente")
    __table_args__ = (
        Index("ix_cli_estado", "estado"),
        Index("ix_cli_ultima_compra", "ultima_compra"),
    )

# ── PRODUCTOS ─────────────────────────────────────────────────────────────────

class Producto(Base):
    __tablename__ = "productos"
    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku                 = Column(String(30),  unique=True, nullable=False, index=True)
    nombre              = Column(String(200), nullable=False)
    descripcion         = Column(Text,        nullable=True)
    descripcion_ia      = Column(Text,        nullable=True)   # generada por Claude
    categoria           = Column(String(80),  nullable=True)
    genero              = Column(String(20),  nullable=True)   # mujer/hombre/unisex
    tallas_disponibles  = Column(JSON, default=list)
    colores             = Column(JSON, default=list)
    precio_costo        = Column(Float, nullable=False)
    precio_venta        = Column(Float, nullable=False)
    precio_promo        = Column(Float, nullable=True)
    stock               = Column(JSON, default=dict)           # {"S_negro": 5}
    imagenes            = Column(JSON, default=list)           # URLs Cloudflare R2
    video_url           = Column(String(500), nullable=True)
    activo              = Column(Boolean, default=True)
    destacado           = Column(Boolean, default=False)
    ventas_total        = Column(Integer, default=0)
    fecha_creacion      = Column(DateTime, default=datetime.utcnow)
    meta_tags           = Column(JSON, default=list)
    items_pedido        = relationship("ItemPedido", back_populates="producto")

# ── PEDIDOS ───────────────────────────────────────────────────────────────────

class Pedido(Base):
    __tablename__ = "pedidos"
    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    numero              = Column(String(20),  unique=True, nullable=False)
    cliente_id          = Column(UUID(as_uuid=True), ForeignKey("clientes.id"), nullable=False)
    canal               = Column(Enum(CanalVenta),   nullable=False)
    estado              = Column(Enum(PedidoEstado), default=PedidoEstado.pendiente)
    subtotal            = Column(Float, nullable=False)
    costo_envio         = Column(Float, default=0.0)
    descuento           = Column(Float, default=0.0)
    total               = Column(Float, nullable=False)
    ciudad_entrega      = Column(String(80),  nullable=True)
    direccion_entrega   = Column(Text,        nullable=True)
    metodo_pago         = Column(Enum(MetodoPago), nullable=True)
    referencia_pago     = Column(String(200), nullable=True)
    codigo_seguimiento  = Column(String(100), nullable=True)
    fecha_pedido        = Column(DateTime, default=datetime.utcnow)
    fecha_pago          = Column(DateTime, nullable=True)
    fecha_envio         = Column(DateTime, nullable=True)
    fecha_entrega       = Column(DateTime, nullable=True)
    cliente             = relationship("Cliente",    back_populates="pedidos")
    items               = relationship("ItemPedido", back_populates="pedido")
    pagos               = relationship("Pago",       back_populates="pedido")
    __table_args__ = (
        Index("ix_ped_estado", "estado"),
        Index("ix_ped_fecha",  "fecha_pedido"),
        Index("ix_ped_cliente","cliente_id"),
    )

class ItemPedido(Base):
    __tablename__ = "items_pedido"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id       = Column(UUID(as_uuid=True), ForeignKey("pedidos.id"),   nullable=False)
    producto_id     = Column(UUID(as_uuid=True), ForeignKey("productos.id"), nullable=False)
    talla           = Column(String(5),  nullable=True)
    color           = Column(String(50), nullable=True)
    cantidad        = Column(Integer, nullable=False, default=1)
    precio_unitario = Column(Float,   nullable=False)
    subtotal        = Column(Float,   nullable=False)
    pedido          = relationship("Pedido",   back_populates="items")
    producto        = relationship("Producto", back_populates="items_pedido")

# ── PAGOS ─────────────────────────────────────────────────────────────────────

class Pago(Base):
    __tablename__ = "pagos"
    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id       = Column(UUID(as_uuid=True), ForeignKey("pedidos.id"), nullable=False)
    metodo          = Column(Enum(MetodoPago), nullable=False)
    monto           = Column(Float,   nullable=False)
    moneda          = Column(String(5), default="BOB")
    referencia      = Column(String(200), nullable=True)
    captura_url     = Column(String(500), nullable=True)
    verificado      = Column(Boolean, default=False)
    verificado_por  = Column(String(50), nullable=True)   # "ia" | "humano"
    fecha           = Column(DateTime, default=datetime.utcnow)
    datos_extra     = Column(JSON, default=dict)
    pedido          = relationship("Pedido", back_populates="pagos")

# ── CONVERSACIONES ────────────────────────────────────────────────────────────

class Conversacion(Base):
    __tablename__ = "conversaciones"
    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cliente_id       = Column(UUID(as_uuid=True), ForeignKey("clientes.id"), nullable=True)
    canal            = Column(Enum(CanalVenta), nullable=False)
    session_id       = Column(String(100), nullable=False, index=True)
    mensajes         = Column(JSON, default=list)
    intencion        = Column(String(50), nullable=True)
    convertido       = Column(Boolean, default=False)
    pedido_id        = Column(UUID(as_uuid=True), ForeignKey("pedidos.id"), nullable=True)
    escalado_humano  = Column(Boolean, default=False)
    fecha_inicio     = Column(DateTime, default=datetime.utcnow)
    fecha_ultimo     = Column(DateTime, default=datetime.utcnow)
    cliente          = relationship("Cliente", back_populates="conversaciones")

# ── SEGUIMIENTOS ──────────────────────────────────────────────────────────────

class Seguimiento(Base):
    __tablename__ = "seguimientos"
    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cliente_id       = Column(UUID(as_uuid=True), ForeignKey("clientes.id"), nullable=False)
    tipo             = Column(String(50), nullable=False)   # carrito_abandonado|postventa|reactivacion
    estado           = Column(String(20), default="pendiente")
    canal            = Column(Enum(CanalVenta), default=CanalVenta.whatsapp)
    mensaje          = Column(Text,    nullable=True)
    respuesta        = Column(Text,    nullable=True)
    convertido       = Column(Boolean, default=False)
    intento_num      = Column(Integer, default=1)
    fecha_programada = Column(DateTime, nullable=False)
    fecha_enviado    = Column(DateTime, nullable=True)
    datos_extra      = Column(JSON,    default=dict)
    cliente          = relationship("Cliente", back_populates="seguimientos")
    __table_args__ = (Index("ix_seg_estado_fecha", "estado", "fecha_programada"),)

# ── CAMPAÑAS ──────────────────────────────────────────────────────────────────

class Campana(Base):
    __tablename__ = "campanas"
    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre              = Column(String(200), nullable=False)
    tipo                = Column(String(50),  nullable=False)
    canal               = Column(Enum(CanalVenta), nullable=False)
    segmento            = Column(JSON, default=dict)
    mensaje_template    = Column(Text, nullable=False)
    imagen_url          = Column(String(500), nullable=True)
    descuento_pct       = Column(Float, nullable=True)
    estado              = Column(String(20), default="borrador")
    total_enviados      = Column(Integer, default=0)
    total_convertidos   = Column(Integer, default=0)
    ingresos_generados  = Column(Float,   default=0.0)
    fecha_inicio        = Column(DateTime, nullable=True)
    fecha_fin           = Column(DateTime, nullable=True)
    fecha_creacion      = Column(DateTime, default=datetime.utcnow)

# ── CONTENIDO PROGRAMADO ──────────────────────────────────────────────────────

class ContenidoProgramado(Base):
    __tablename__ = "contenido_programado"
    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    red_social       = Column(String(30), nullable=False)
    tipo             = Column(String(30), nullable=False)
    caption          = Column(Text,       nullable=True)
    hashtags         = Column(JSON, default=list)
    media_url        = Column(String(500), nullable=True)
    estado           = Column(String(20),  default="pendiente")
    fecha_programada = Column(DateTime,    nullable=False)
    fecha_publicado  = Column(DateTime,    nullable=True)
    post_id_externo  = Column(String(200), nullable=True)
    metricas         = Column(JSON, default=dict)
    generado_por_ia  = Column(Boolean, default=False)

# ── HELPERS ───────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    Base.metadata.create_all(bind=engine)
