# Nopal Leads — Guía rápida

Sistema backend para captura y clasificación de leads desde Facebook Messenger, Instagram Direct, WhatsApp Business y comentarios en posts.

## 1. Requisitos previos

- [Docker](https://docs.docker.com/get-docker/) y [Docker Compose](https://docs.docker.com/compose/install/) instalados
- Cuenta en [Meta for Developers](https://developers.facebook.com/) con una app configurada (Messenger, Instagram, WhatsApp según necesites)
- API Key de [OpenAI](https://platform.openai.com/api-keys) (solo se usa cuando las reglas por keywords no clasifican el mensaje)

## 2. Configuración inicial

```bash
cp .env.example .env
```

Edita `.env` y completa cada variable:

| Variable | Dónde obtenerla |
|----------|-----------------|
| `META_VERIFY_TOKEN` | Token que tú defines; debe coincidir con el que configures en el panel de Meta al suscribir el webhook |
| `META_APP_SECRET` | Meta Developers → Tu app → Configuración → Básica → **Clave secreta de la app** |
| `META_PAGE_ACCESS_TOKEN` | Meta Developers → Tu app → Messenger / WhatsApp → Generar token de página o de sistema |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta Developers → WhatsApp → API Setup → **Phone number ID** |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/api-keys) |
| `OPENAI_MODEL` | Modelo para clasificación (default: `gpt-4o-mini`). Ej: `gpt-4o`, `gpt-3.5-turbo` |
| `SESSION_IDLE_TIMEOUT_MINUTES` | Minutos máximos de inactividad antes de cerrar conversación (default: `10`) |
| `SESSION_MAX_DURATION_MINUTES` | Minutos máximos de vida total de la conversación (default: `30`) |
| `MESSAGE_BURST_INACTIVITY_SECONDS` | Ventana de inactividad para unir mensajes fragmentados del mismo remitente (default: `1.5`) |
| `MESSAGE_BURST_MAX_WAIT_SECONDS` | Tiempo máximo total para esperar más fragmentos antes de procesar (default: `4.0`) |
| `MESSAGE_BURST_MAX_MESSAGES` | Límite de fragmentos por lote antes de forzar procesamiento inmediato (default: `5`) |
| `HANDOFF_IDLE_TIMEOUT_MINUTES` | Minutos para mantener al bot en silencio tras intervención humana en el hilo (default: `30`) |
| `BOT_OUTBOUND_EVENT_TTL_SECONDS` | Ventana para reconocer echoes del propio bot y evitar bloqueo falso (default: `180`) |
| `MAX_CLARIFICATION_ATTEMPTS` | Máximo de intentos de aclaración cuando el motivo no es claro (default: `3`) |
| `MAX_TOPIC_CHANGES` | Máximo de cambios de tema permitidos por sesión antes de cierre amable (default: `2`) |
| `JWT_SECRET_KEY` | Secreto para firmar tokens JWT de acceso interno |
| `JWT_ALGORITHM` | Algoritmo JWT (default: `HS256`) |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Vigencia del token JWT en minutos (default: `480`) |
| `SUPERUSER_USERNAME` | Usuario del superadmin sembrado automáticamente |
| `SUPERUSER_PASSWORD` | Contraseña del superadmin sembrado automáticamente |
| `POSTGRES_*` / `DATABASE_URL` | Valores por defecto del `.env.example` funcionan con Docker Compose |

## 3. Levantar el proyecto

```bash
docker compose up --build
```

- La API queda en **http://localhost:8000**
- Documentación interactiva: **http://localhost:8000/docs**
- Health check: **http://localhost:8000/health**
- PostgreSQL ejecuta `app/db/migrations.sql` automáticamente al crear el volumen por primera vez
- Si ya tenías un volumen de BD existente, aplica migraciones manualmente:

```bash
docker compose exec -T db psql -U postgres -d nopal_leads -f app/db/migrations.sql
```

- En startup, la API ejecuta seeder de auth y crea/actualiza el superusuario con `SUPERUSER_USERNAME` y `SUPERUSER_PASSWORD`

## 4. Configurar el webhook en Meta

Meta requiere una **URL pública HTTPS** para enviar eventos.

### Desarrollo local con ngrok

```bash
ngrok http 8000
```

Copia la URL HTTPS que ngrok muestra (ej. `https://a1b2c3d4.ngrok-free.app`) y en Meta Developers:

1. Ve a tu app → **Webhooks** (o WhatsApp → Configuration → Webhook) y elige el **producto/objeto correcto** (no `User`)
2. **Callback URL:** `https://TU-DOMINIO-NGROK/webhook/meta`
3. **Verify token:** el mismo valor que `META_VERIFY_TOKEN` en tu `.env`
4. Suscríbete así (producto → campos):

| Producto en Webhooks | Para qué se usa | Campo(s) a suscribir |
|---|---|---|
| **WhatsApp Business Account** | Mensajes de WhatsApp | `messages` |
| **Page** | Mensajes de Messenger (base) | `messages` |
| **Page** | Detección de intervención humana y handover (Messenger) | `message_echoes`, `messaging_handovers`, `standby` |
| **Page** | Comentarios/actividad de página de Facebook | `feed` |
| **Instagram** | Mensajes de Instagram Direct | `messages` |
| **Instagram** | Comentarios de Instagram | `comments` |

Notas:
- Si no vas a usar un canal, no lo suscribas.
- Para que el backend pueda detectar respuesta humana de la página y silenciar al bot automáticamente en Messenger, debes suscribir también `message_echoes`, `messaging_handovers` y `standby` en `Page`.
- En algunas cuentas no aparece `comments` en `Page`; en ese caso usa `feed` para comentarios de Facebook.
- `messaging` no se suscribe como campo en versiones recientes; es parte del payload (`entry.messaging`).

En producción, coloca Nginx (u otro proxy) con TLS delante del contenedor `api` en el puerto 8000.

## 5. Importar Postman

1. Abre Postman → **Import**
2. Importa `nopal_leads.postman_collection.json` y `nopal_leads.postman_environment.json`
3. Selecciona el ambiente **nopal_leads** en el selector superior derecho
4. Ajusta `meta_verify_token` en el ambiente para que coincida con tu `.env`

**Documentación detallada de cada endpoint, ejemplos curl y flujos completos:** [API.md](./API.md)

### Orden recomendado de pruebas

1. **00 — Health check**
2. **00.1 — Auth login (JWT)**
3. **01 — Verificación Meta (GET)**
4. **02 — Crear o recuperar sesión**
5. **03 — Extraer datos** / **04 — Clasificar texto**
6. **05–07 — Simular mensajes** (flujo conversacional)
7. **08 — Enviar respuesta**

Endpoints protegidos por JWT: `/classify`, `/extract`, `/session`, `/respond`, `/handoff`, `/reports/leads`.
Endpoints públicos: `/health`, `/webhook/meta`.

## 6. Flujo de prueba end-to-end

### Paso 1: Simular mensaje de WhatsApp

Request **Simular mensaje entrante** con body:

```json
{
  "sender_id": "123456789",
  "texto": "cuánto el kilo en Puebla",
  "plataforma": "whatsapp"
}
```

**Respuesta esperada:**

```json
{
  "status": "ok",
  "processed": 0,
  "queued": 1,
  "results": [{
    "lead_id": "...",
    "session_id": "...",
    "tipo": "lead",
    "extraido": { "telefono": null, "cantidad": null, "ciudad": "Puebla" },
    "respuesta_enviada": false,
    "respuesta": "¡Claro! ¿Cuántos kilos de nopal necesitas aproximadamente?"
  }]
}
```

Con agregación por ráfagas activa, `processed` puede regresar `0` y `queued` mayor a `0` mientras el sistema espera más fragmentos del mismo remitente. El lote se procesa automáticamente al cumplir la ventana de inactividad o los límites configurados.

(`respuesta_enviada: true` solo si `META_PAGE_ACCESS_TOKEN` está configurado.)

### Paso 2: Completar datos con otro mensaje

Envía otro POST con el mismo `sender_id`:

```json
{
  "sender_id": "123456789",
  "texto": "necesito 40 kilos, mi número es 2221234567",
  "plataforma": "whatsapp"
}
```

La sesión acumula cantidad y teléfono sin borrar la ciudad ya capturada.

### Paso 3: Verificar en la base de datos

```bash
docker compose exec db psql -U postgres -d nopal_leads -c "SELECT created_at, plataforma, direction, categoria, sender_id, texto FROM messages ORDER BY created_at DESC LIMIT 10;"
```

```bash
docker compose exec db psql -U postgres -d nopal_leads -c "SELECT ci.sender_id, ci.plataforma, c.telefono, o.cantidad, o.ciudad, conv.status FROM contact_identities ci JOIN contacts c ON c.id = ci.contact_id JOIN conversations conv ON conv.contact_identity_id = ci.id LEFT JOIN orders o ON o.conversation_id = conv.id AND o.status = 'draft' ORDER BY conv.last_message_at DESC LIMIT 20;"
```

```bash
docker compose exec db psql -U postgres -d nopal_leads -c "SELECT oi.order_id, oi.product_code, oi.product_name, oi.quantity, oi.unit, o.status FROM order_items oi JOIN orders o ON o.id = oi.order_id ORDER BY oi.updated_at DESC LIMIT 20;"
```

## Estructura del proyecto

```
app/
  main.py
  routes/
    webhook.py
    internal.py
  constants/
    classification.py
  services/
    classifier.py
    extractor.py
    responder.py
    session_manager.py
    meta_webhook.py
    pipeline.py
  models/
    lead.py
    session.py
  db/
    connection.py
    migrations.sql
Dockerfile
docker-compose.yml
.env.example
nopal_leads.postman_collection.json
nopal_leads.postman_environment.json
API.md
```

## Notas

- Las reglas por keywords se evalúan **antes** de llamar a OpenAI (`app/constants/classification.py`)
- Los eventos duplicados (mismo `event_id`) se ignoran automáticamente
- El webhook POST tiene rate limit de **60 requests/minuto** por IP
- En producción, usa siempre HTTPS detrás de un reverse proxy (Nginx recomendado)
- Si vienes de una versión anterior (con tablas `sessions`/`leads`), recrea la BD para aplicar este esquema: `docker compose down -v && docker compose up --build`
