"""
shared/utils/helpers.py
Utilidades compartidas entre microservicios.
"""
import hashlib, hmac, random, string, re
from datetime import datetime, timedelta

def generar_numero_pedido(prefix: str = "RB") -> str:
    now = datetime.utcnow()
    rand = "".join(random.choices(string.digits, k=4))
    return f"{prefix}-{now.year}-{now.strftime('%m%d')}-{rand}"

def limpiar_telefono(tel: str) -> str:
    """Normaliza a formato 591XXXXXXXX."""
    digits = re.sub(r"\D", "", tel)
    if digits.startswith("591"):
        return digits
    if len(digits) == 8:
        return f"591{digits}"
    return digits

def calcular_envio(ciudad: str) -> float:
    """Costos de envío fijos por ciudad Bolivia (Bs.)."""
    tabla = {
        "la paz": 15, "el alto": 15, "cochabamba": 25,
        "santa cruz": 25, "oruro": 25, "potosí": 30,
        "sucre": 30, "tarija": 35, "beni": 40, "pando": 45,
    }
    return tabla.get(ciudad.lower().strip(), 30)

def verificar_firma_meta(payload: bytes, firma: str, secret: str) -> bool:
    """Verifica firma HMAC-SHA256 de webhooks Meta."""
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", firma)

def segmentar_cliente(ltv: float, num_pedidos: int, dias_inactivo: int) -> str:
    if ltv >= 500 or num_pedidos >= 5:
        return "vip"
    if dias_inactivo > 30:
        return "inactivo"
    if num_pedidos >= 1:
        return "activo"
    return "nuevo"

def calcular_descuento(estado_cliente: str, monto: float) -> float:
    descuentos = {"vip": 0.10, "activo": 0.05, "nuevo": 0.0, "inactivo": 0.15}
    return round(monto * descuentos.get(estado_cliente, 0.0), 2)

def dias_desde(fecha: datetime) -> int:
    if not fecha:
        return 9999
    return (datetime.utcnow() - fecha).days
