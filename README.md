# 🛍️ RopaBolivia — Plataforma 24/7 con IA
## Microservicios Python · Sin n8n · GitHub → EasyPanel

---

## ARQUITECTURA COMPLETA (sin n8n)

```
CANALES ENTRADA
├── WhatsApp Business API  → whatsapp-bot     :3001
├── Instagram DM           → meta-webhook     :3002
├── Facebook Messenger     → meta-webhook     :3002
├── TikTok (comentarios)   → meta-webhook     :3002  [futuro: TikTok API]
└── YouTube Shorts         → content-scheduler:3007  [publicación]

MICROSERVICIOS
├── whatsapp-bot      :3001  Bot IA Claude — ventas conversacionales 24/7
├── meta-webhook      :3002  DMs Instagram + Facebook + comentarios
├── catalog-api       :3003  Productos, stock, feed XML Meta, descripción IA
├── crm-api           :3004  Clientes, pedidos, conversaciones, seguimientos
├── payment-gateway   :3005  Tigo Money, QR BNB, transferencia
├── ai-recommender    :3006  Recomendaciones, scripts contenido, copy campañas
├── content-scheduler :3007  Calendario 30 días, publicación IG/FB automática
├── campaign-engine   :3008  Campañas masivas, secuencias automáticas
├── analytics-api     :3009  KPIs, dashboard gerencia, ROI
└── notification-worker:3010 Cron jobs: carritos, reactivación, flash sale

DATOS COMPARTIDOS
├── PostgreSQL (Supabase free tier o EasyPanel)
├── Redis (Upstash free o EasyPanel)
└── Cloudflare R2 (imágenes catálogo)
```

---

## DEPLOY EN EASYPANEL (paso a paso)

### 1. Preparar GitHub
```bash
git clone https://github.com/tu-usuario/ropa-bolivia
cd ropa-bolivia
git add .
git commit -m "initial: microservicios ropa bolivia"
git push origin main
```

### 2. Instalar EasyPanel en tu VPS
```bash
# En VPS Ubuntu 22.04 (mínimo 4GB RAM, 2 CPU — $12-20/mes en Hetzner/DigitalOcean)
curl -sSL https://get.easypanel.io | sh
# Luego abrir: http://IP_DE_TU_VPS:3000
```

### 3. Crear proyecto en EasyPanel
1. Panel → **New Project** → nombre: `ropa-bolivia`
2. **Databases** → crear PostgreSQL → copiar `DATABASE_URL`
3. **Databases** → crear Redis → copiar `REDIS_URL`

### 4. Crear cada servicio
Por cada servicio en `services/`:
1. **New Service → App**
2. Conectar a GitHub repo
3. Configurar **Build Path** = `services/nombre-servicio`
4. Agregar **Variables de entorno** (ver tabla abajo)
5. Configurar **Domain** con subdominio
6. Clic **Deploy**

### 5. Configurar webhooks en Meta
- Instagram/Facebook: `https://meta.ropabolivia.com/webhook`
- WhatsApp: `https://wa.ropabolivia.com/webhook`
- Token de verificación: `ropa_bolivia_2025` / `ropa_bolivia_meta`

### 6. Configurar webhook Tigo Money
- URL: `https://pay.ropabolivia.com/webhook/tigo`
- (Contactar a Tigo Bolivia para activar notificaciones)

---

## VARIABLES DE ENTORNO

```env
# Base de datos
DATABASE_URL=postgresql://user:pass@host:5432/ropa_bolivia
REDIS_URL=redis://default:pass@host:6379

# IA
ANTHROPIC_API_KEY=sk-ant-api03-...

# WhatsApp Business API (Meta)
WA_ACCESS_TOKEN=EAAxxxxx...
WA_PHONE_NUMBER_ID=123456789
WA_VERIFY_TOKEN=ropa_bolivia_2025

# Instagram + Facebook
META_APP_SECRET=abc123...
META_PAGE_ACCESS_TOKEN=EAAxxxxx...
META_VERIFY_TOKEN=ropa_bolivia_meta
META_PAGE_ID=123456789
META_IG_ACCOUNT_ID=987654321

# Pagos Bolivia
TIGO_MONEY_API_KEY=tigo_key_...
TIGO_MONEY_MERCHANT_ID=merchant_123
BNB_MERCHANT_CODE=bnb_code_...

# Storage
CLOUDFLARE_R2_ACCESS_KEY=...
CLOUDFLARE_R2_SECRET_KEY=...
CLOUDFLARE_R2_BUCKET=ropa-bolivia-media

# Notificaciones dueño
OWNER_WHATSAPP=59170000000
```

---

## COSTO MENSUAL TOTAL

| Componente            | Servicio                  | Costo/mes USD |
|-----------------------|---------------------------|---------------|
| VPS EasyPanel         | Hetzner CX31 (4GB/2CPU)   | $12           |
| Base de datos         | Supabase (500MB free)     | $0            |
| Redis                 | Upstash (10K cmd/día free)| $0            |
| Imágenes              | Cloudflare R2             | $0-2          |
| Claude Haiku (bot)    | Anthropic API             | $5-15         |
| Claude Sonnet (reco.) | Anthropic API             | $5-10         |
| WhatsApp Business     | Meta Cloud API            | $0-5          |
| **TOTAL**             |                           | **$22-44**    |

---

## FLUJOS AUTOMATIZADOS (sin n8n)

### Flujo 1: Cliente nuevo WhatsApp
```
Mensaje WA → whatsapp-bot webhook → Claude genera respuesta
           → crm-api registra cliente → Redis guarda historial
           → Respuesta enviada en <15 seg
           → campaign-engine inicia secuencia "nuevo_cliente"
           → notification-worker programa 3 seguimientos automáticos
```

### Flujo 2: Venta completa
```
Cliente confirma → crm-api crea pedido
                → payment-gateway genera instrucciones de pago
                → whatsapp-bot envía instrucciones al cliente
                → Cliente paga → webhook Tigo confirma
                → payment-gateway verifica → crm-api marca "pagado"
                → notification-worker programa seguimiento 48h post-entrega
```

### Flujo 3: Carrito abandonado (automático)
```
notification-worker (cada 3h) → detecta conversaciones sin conversión
                              → ai-recommender genera copy personalizado
                              → whatsapp-bot envía mensaje urgencia
                              → Secuencia: 3h → 24h (10% OFF) → 72h (15% OFF)
```

### Flujo 4: Flash sale automático (viernes 20:00)
```
notification-worker (cron viernes 20:00)
→ crm-api obtiene todos los clientes activos
→ ai-recommender genera copy flash sale
→ whatsapp-bot broadcast masivo
→ campaign-engine registra campaña y métricas
```

### Flujo 5: Reporte diario (09:00 AM)
```
notification-worker (cron 09:00)
→ analytics-api obtiene KPIs del día
→ whatsapp-bot envía resumen al dueño
```

### Flujo 6: Calendario de contenido
```
content-scheduler genera 30 días con IA
→ ai-recommender crea scripts por plataforma
→ Posts se publican automáticamente en IG/FB en fecha/hora programada
→ TikTok: scripts se exportan para grabar manualmente
```

---

## ESTRATEGIA DE NEGOCIO

### Modelo: Social Commerce D2C + Dropshipping local
- **Fase 1**: Dropshipping con 2-3 proveedores La Paz/SCZ
- **Fase 2**: Inventario propio del top 10 productos
- **Fase 3**: Marca propia + fabricación local

### Nichos más rentables Bolivia 2025
1. **Ropa casual mujer 18-35** — demanda máxima, margen 55-65%
2. **Plus size** — baja competencia, margen 60-70%
3. **Deportivo unisex** — alta rotación, margen 50-60%
4. **Moda urbana hombre** — competencia baja, margen 55-65%
5. **Ropa fiesta/salida** — ticket alto, margen 65-75%

### Márgenes recomendados
- Precio venta = Costo × 2.5 a 3.5
- Margen bruto objetivo: 55-65%
- Margen neto (después costos): 35-45%
- Costo herramientas ($22-44): cubrir con 3-5 ventas/mes

### Métodos de pago Bolivia
- Tigo Money (número 70123456)
- QR BNB (Banco Nacional de Bolivia)
- Transferencia Banco Unión

---

## CALENDARIO CONTENIDO 30 DÍAS

### Frecuencia por red
| Red       | Posts/día | Tipo          | Mejor horario |
|-----------|-----------|---------------|---------------|
| TikTok    | 3-5       | Reels/Lives   | 9h, 13h, 20h  |
| Instagram | 2-3       | Reels+Stories | 8h, 12h, 19h  |
| Facebook  | 2         | Posts+Lives   | 10h, 20h      |
| YouTube   | 1-2       | Shorts        | 11h, 17h      |

### Guión LIVE de ventas (60 min)
- **0-5 min**: "¡Buenas noches Bolivia! 🔥 Inviten a sus amigas. Hoy tenemos X prendas con precio de LIVE"
- **5-10 min**: Calentamiento — "¿De dónde se conectan? Comenten su ciudad"
- **10-40 min**: Mostrar prendas — "Esta polera, precio normal Bs.120, hoy Bs.85. Tallas S-M-L disponibles. Escriban su talla"
- **40-55 min**: Flash deal — "Próximos 3 minutos: combo jean+blusa Bs.180. Solo 10 combos. Escriban COMBO"
- **55-60 min**: "Para pedir escriban al WhatsApp +591 70123456 con su nombre, talla y ciudad"

---

## ESCALABILIDAD

### Fase 1: 0→100 ventas/mes (Mes 1-3)
- 1 persona, $22/mes herramientas
- TikTok orgánico + WhatsApp bot básico
- Dropshipping, sin stock propio
- Goal: encontrar producto estrella

### Fase 2: 100→1.000 ventas/mes (Mes 4-9)
- 2-3 personas, $50-100/mes
- Meta Ads $5-20/día con ROAS objetivo 3×
- Inventario propio top 10 productos
- CRM completo, campañas automáticas activas

### Fase 3: 1.000→10.000 ventas/mes (Mes 10-24)
- 5-10 personas, $300-800/mes
- Agencia creativa, 2 editores video
- Warehouse propio La Paz + operador SCZ
- Marca propia, fabricante local/peruano
- 20+ influencers programa de afiliados

---

## IA AVANZADA — 5 AGENTES ESPECIALIZADOS

| Agente | Modelo | Función | Microservicio |
|--------|--------|---------|---------------|
| Vendedor 24/7 | Claude Haiku | Chat ventas WA/IG/FB | whatsapp-bot |
| Recomendador | Claude Sonnet | Recomendaciones personalizadas | ai-recommender |
| Content Creator | Claude Sonnet | Scripts TikTok/IG/FB/YT | ai-recommender |
| Analista tendencias | Claude Sonnet | Análisis ventas + inventario | ai-recommender |
| Campaign Manager | Claude Haiku | Copy campañas masivas | ai-recommender |

Todos orquestados por **notification-worker** (APScheduler) y **campaign-engine** (FastAPI async).

---

## COMANDOS ÚTILES

```bash
# Desarrollo local
docker-compose up --build

# Probar bot WhatsApp (ngrok para tunnel)
ngrok http 3001
# Configurar en Meta: https://TU-NGROK.ngrok.io/webhook

# Verificar salud de todos los servicios
for port in 3001 3002 3003 3004 3005 3006 3007 3008 3009 3010; do
  curl -s http://localhost:$port/health | python3 -m json.tool
done

# Ejecutar job manual (carrito abandonado)
curl -X POST http://localhost:3010/trigger/carritos

# Generar calendario 30 días
curl -X POST http://localhost:3007/calendario/generar \
  -H "Content-Type: application/json" \
  -d '{"dias": 30, "frecuencia_diaria": 3}'

# Ver KPIs últimos 30 días
curl http://localhost:3009/kpis?dias=30 | python3 -m json.tool
```
