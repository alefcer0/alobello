# Nopal Leads — Documentación de API

Referencia de endpoints, ejemplos de uso con Postman/curl y flujos completos de prueba.

**Base URL local:** `http://localhost:8000`  
**Swagger interactivo:** http://localhost:8000/docs  
**Health check:** http://localhost:8000/health

---

## Configuración en Postman

1. Importa `nopal_leads.postman_collection.json` y `nopal_leads.postman_environment.json`.
2. Selecciona el ambiente **nopal_leads**.
3. Ajusta las variables:

| Variable | Ejemplo | Uso |
|----------|---------|-----|
| `base_url` | `http://localhost:8000` | URL base de la API |
| `meta_verify_token` | `leads-test-052026` | Debe coincidir con `META_VERIFY_TOKEN` en `.env` |
| `test_sender_id` | `123456789` | ID de usuario simulado (como un `sender_id` de Meta) |
| `test_platform` | `whatsapp` | `whatsapp`, `facebook` o `instagram` |

Variables en `.env` (servidor):

| Variable | Default | Uso |
|----------|---------|-----|
| `OPENAI_API_KEY` | — | Clasificación cuando no hay match por keywords |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo de chat usado en esa clasificación |
| `SESSION_IDLE_TIMEOUT_MINUTES` | `10` | Cierre de conversación por inactividad |
| `SESSION_MAX_DURATION_MINUTES` | `30` | Cierre de conversación por tiempo total desde el inicio |
| `MESSAGE_BURST_INACTIVITY_SECONDS` | `1.5` | Ventana de inactividad para unir mensajes fragmentados por remitente |
| `MESSAGE_BURST_MAX_WAIT_SECONDS` | `4.0` | Tiempo máximo total para esperar fragmentos adicionales |
| `MESSAGE_BURST_MAX_MESSAGES` | `5` | Cantidad máxima de fragmentos por lote antes de forzar procesamiento |
| `HANDOFF_IDLE_TIMEOUT_MINUTES` | `30` | Tiempo de silencio del bot tras detectar intervención humana |
| `BOT_OUTBOUND_EVENT_TTL_SECONDS` | `180` | Ventana para identificar echoes del propio bot y no bloquear por falso positivo |
| `JWT_SECRET_KEY` | — | Secreto para firmar JWT (requerido) |
| `JWT_ALGORITHM` | `HS256` | Algoritmo de firma JWT |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | Vigencia del token JWT |
| `SUPERUSER_USERNAME` | — | Usuario superadmin sembrado al arranque |
| `SUPERUSER_PASSWORD` | — | Contraseña superadmin sembrada al arranque |

> Si cambias `.env`, recrea el contenedor: `docker compose up -d --force-recreate api`

---

## Resumen de endpoints

| Método | Ruta | Propósito |
|--------|------|-----------|
| GET | `/health` | Comprobar que la API está viva |
| GET | `/webhook/meta` | Verificación del webhook (Meta) |
| POST | `/webhook/meta` | Recibir mensajes (Meta real o simulado) |
| POST | `/auth/login` | Iniciar sesión y obtener JWT |
| POST | `/classify` | Clasificar texto aislado |
| POST | `/extract` | Extraer teléfono, kilos y ciudad |
| POST | `/session` | Crear o recuperar sesión de usuario |
| POST | `/respond` | Generar y enviar respuesta automática |
| POST | `/handoff` | Bloquear/desbloquear respuestas automáticas por conversación |
| GET | `/reports/leads` | Reporte de ventas, leads caídos/descartados e interacciones |

> Endpoints internos protegidos por JWT: `/classify`, `/extract`, `/session`, `/respond`, `/handoff`, `/reports/leads`.
> Endpoints públicos operativos: `/health`, `/webhook/meta` (GET/POST).

---

## POST `/auth/login` — Obtener token JWT

Usa las credenciales `SUPERUSER_USERNAME` y `SUPERUSER_PASSWORD` configuradas en `.env`.

### Request

```http
POST http://localhost:8000/auth/login
Content-Type: application/json

{
  "username": "admin",
  "password": "change_this_superuser_password"
}
```

### Respuesta `200`

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_at": "2026-09-14T18:00:00Z",
  "expires_in_seconds": 28800,
  "username": "admin",
  "is_superuser": true
}
```

Usa el token en headers:

```http
Authorization: Bearer <jwt>
```

---

## GET `/health`

Comprueba que el servidor responde. No requiere autenticación.

### Request

```http
GET http://localhost:8000/health
```

### Respuesta `200`

```json
{
  "status": "ok"
}
```

### curl

```bash
curl http://localhost:8000/health
```

---

## GET `/webhook/meta` — Verificación Meta

Meta llama a este endpoint al **suscribir o verificar** el webhook. Tu API debe devolver el valor de `hub.challenge` en texto plano si el token coincide.

### Query parameters

| Parámetro | Obligatorio | Descripción |
|-----------|-------------|-------------|
| `hub.mode` | Sí | Siempre `subscribe` en la verificación |
| `hub.verify_token` | Sí | Debe ser igual a `META_VERIFY_TOKEN` |
| `hub.challenge` | Sí | Valor que Meta espera recibir de vuelta (suele ser numérico) |

### Request (Postman / curl)

```http
GET http://localhost:8000/webhook/meta?hub.mode=subscribe&hub.verify_token=leads-test-052026&hub.challenge=1234567890
```

```bash
curl "http://localhost:8000/webhook/meta?hub.mode=subscribe&hub.verify_token=leads-test-052026&hub.challenge=1234567890"
```

### Respuesta exitosa `200`

Cuerpo en **texto plano** (no JSON):

```
1234567890
```

### Errores

| Código | Causa |
|--------|--------|
| `403` | `hub.verify_token` no coincide con `.env` o el contenedor no recargó variables |
| `422` | Falta algún query parameter |

### Ejemplo con ngrok (Meta real)

```
https://TU-DOMINIO.ngrok-free.dev/webhook/meta
```

Mismo query string; Meta lo envía automáticamente al pulsar **Verificar y guardar**.

---

## POST `/webhook/meta` — Mensaje entrante (flujo completo)

Endpoint principal. Procesa en un solo paso:

1. Extracción de datos (teléfono, kilos, ciudad)
2. Clasificación (reglas → OpenAI si hace falta)
3. Guardado en `leads` y actualización de `sessions`
4. Respuesta automática **solo si** `tipo = lead`

### Modo simulado (Postman / pruebas locales)

Envía JSON simple **sin** payload de Meta. No requiere firma `x-hub-signature-256`.

```json
{
  "sender_id": "123456789",
  "texto": "¿Cuánto el kilo de nopal en Puebla?",
  "plataforma": "whatsapp"
}
```

| Campo | Tipo | Valores |
|-------|------|---------|
| `sender_id` | string | ID del remitente en Meta |
| `texto` | string | Mensaje del usuario |
| `plataforma` | string | `whatsapp`, `facebook`, `instagram` |

### Request

```http
POST http://localhost:8000/webhook/meta
Content-Type: application/json

{
  "sender_id": "123456789",
  "texto": "¿Cuánto el kilo de nopal en Puebla?",
  "plataforma": "whatsapp"
}
```

```bash
curl -X POST http://localhost:8000/webhook/meta \
  -H "Content-Type: application/json" \
  -d "{\"sender_id\":\"123456789\",\"texto\":\"¿Cuánto el kilo de nopal en Puebla?\",\"plataforma\":\"whatsapp\"}"
```

### Respuesta exitosa `200`

```json
{
  "status": "ok",
  "processed": 0,
  "queued": 1,
  "results": [
    {
      "lead_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
      "tipo": "lead",
      "extraido": {
        "telefono": null,
        "cantidad": null,
        "ciudad": "Puebla"
      },
      "respuesta_enviada": false,
      "respuesta": "¡Claro! ¿Cuántos kilos de nopal necesitas aproximadamente?"
    }
  ]
}
```

| Campo en `results[]` | Descripción |
|----------------------|-------------|
| `lead_id` | UUID del registro en tabla `leads` |
| `session_id` | UUID de la sesión del usuario |
| `tipo` | `lead`, `proveedor`, `saludo` o `spam` |
| `extraido` | Datos acumulados en la sesión tras este mensaje |
| `respuesta_enviada` | `true` si se envió mensaje a Meta/WhatsApp |
| `respuesta` | Texto generado (aunque no se haya enviado a Meta) |

Campos adicionales en respuesta:

| Campo | Descripción |
|-------|-------------|
| `processed` | Cantidad de lotes procesados inmediatamente en esta request |
| `queued` | Cantidad de eventos encolados pendientes de flush por inactividad/límites |

Con agregación de ráfagas activa, es normal que una request responda con `processed: 0` y `queued > 0`; el procesamiento ocurre automáticamente cuando se cumple la ventana configurada.

### Modo producción (Meta real)

Meta envía un payload con estructura `entry`, `messaging`, etc. La API valida el header:

```http
x-hub-signature-256: sha256=<hmac>
```

Calculado con `META_APP_SECRET`. Sin firma válida → `403`.

### Errores

| Código | Causa |
|--------|--------|
| `400` | JSON inválido |
| `403` | Firma incorrecta (payload real sin simulación) |
| `429` | Rate limit (>60 req/min por IP en este endpoint) |

### Idempotencia

Si llega el mismo `event_id` dos veces, el segundo se ignora:

```json
{
  "status": "ok",
  "processed": 0,
  "results": []
}
```

---

## POST `/classify` — Clasificar texto

Clasifica un mensaje **sin** guardar en BD ni enviar respuesta. Útil para probar reglas y OpenAI.

Las listas de palabras clave viven en **`app/constants/classification.py`** (edítalas ahí y reconstruye la API).

### Request

```http
POST http://localhost:8000/classify
Content-Type: application/json

{
  "texto": "¿Cuánto el kilo de nopal en Guadalajara? Quiero cotizar 30 kilos"
}
```

### Respuesta `200`

```json
{
  "categoria": "lead"
}
```

### Ejemplos por categoría

**Lead** (intención comercial: precio, kilo, comprar, pedido, cotiz…; o producto + intención):

**Spam/hostil** (se evalúa antes que lead: insultos, "no compren", etc. aunque digan "nopal"):

```json
{ "texto": "El nopal es mierda, ojalá no te compren" }
```
→ `{ "categoria": "spam" }`

**Lead** (ejemplo válido):

```json
{ "texto": "¿Cuánto cuesta el nopal por mayoreo?" }
```
→ `{ "categoria": "lead" }`

**Proveedor** (vendo, ofrezco, transporte, distribuyo):

```json
{ "texto": "Vendo nopal limpio, distribuyo en zona metropolitana" }
```
→ `{ "categoria": "proveedor" }`

**Saludo**:

```json
{ "texto": "Hola, buenos días" }
```
→ `{ "categoria": "saludo" }`

**Spam / ambiguo** (sin keyword → OpenAI):

```json
{ "texto": "asdkj123 @@@" }
```
→ `{ "categoria": "spam" }` (si OpenAI está configurado)

### curl

```bash
curl -X POST http://localhost:8000/classify \
  -H "Content-Type: application/json" \
  -d "{\"texto\":\"¿Cuánto el kilo en Toluca?\"}"
```

---

## POST `/extract` — Extraer datos

Extrae del texto: teléfono, cantidad en kilos y ciudad. No guarda en BD.

### Request

```http
POST http://localhost:8000/extract
Content-Type: application/json

{
  "texto": "Hola, soy de Monterrey, necesito 25 kilos de nopal. Mi cel es 81 1234 5678"
}
```

### Respuesta `200`

```json
{
  "telefono": "+528112345678",
  "cantidad": "25 kg",
  "ciudad": "Monterrey"
}
```

Los campos no detectados van en `null`:

```json
{
  "telefono": null,
  "cantidad": null,
  "ciudad": "Puebla"
}
```

Con texto: `"¿cuánto el kilo en Puebla?"`

### curl

```bash
curl -X POST http://localhost:8000/extract \
  -H "Content-Type: application/json" \
  -d "{\"texto\":\"necesito 50 kilos en CDMX, tel 55 1234 5678\"}"
```

---

## POST `/session` — Crear o recuperar sesión

Obtiene la sesión activa de un `sender_id` o crea una nueva. Las sesiones expiran tras **48 horas** sin actividad.

### Request

```http
POST http://localhost:8000/session
Content-Type: application/json

{
  "sender_id": "123456789",
  "plataforma": "whatsapp"
}
```

### Respuesta `200` — sesión nueva

```json
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "sender_id": "123456789",
  "plataforma": "whatsapp",
  "ultimo_contacto": "2026-05-24T18:30:00",
  "activa": true,
  "telefono": null,
  "cantidad": null,
  "ciudad": null,
  "nombre": null,
  "handoff_state": "auto",
  "created": true
}
```

### Respuesta `200` — sesión existente

Misma estructura con `"created": false` y campos ya capturados si los hay:

```json
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "sender_id": "123456789",
  "plataforma": "whatsapp",
  "ultimo_contacto": "2026-05-24T18:45:00",
  "activa": true,
  "telefono": "+522221234567",
  "cantidad": "40 kg",
  "ciudad": "Puebla",
  "nombre": null,
  "handoff_state": "human_locked",
  "created": false
}
```

### curl

```bash
curl -X POST http://localhost:8000/session \
  -H "Content-Type: application/json" \
  -d "{\"sender_id\":\"123456789\",\"plataforma\":\"whatsapp\"}"
```

---

## POST `/handoff` — Bloquear o desbloquear respuestas del bot

Permite forzar control humano por conversación (por `sender_id` + `plataforma`).

### Request

```http
POST http://localhost:8000/handoff
Content-Type: application/json

{
  "sender_id": "123456789",
  "plataforma": "whatsapp",
  "locked": true,
  "reason": "agente_humano_toma_chat"
}
```

### Respuesta `200`

```json
{
  "sender_id": "123456789",
  "plataforma": "whatsapp",
  "conversation_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "handoff_state": "human_locked",
  "handoff_until": "2026-05-24T19:30:00"
}
```

Para reactivar respuestas automáticas:

```json
{
  "sender_id": "123456789",
  "plataforma": "whatsapp",
  "locked": false
}
```

---

## GET `/reports/leads` — Reporte consolidado de leads

Devuelve un reporte filtrable para operación/comercial con:

- ventas (`lead_outcome = sale`)
- leads caídos (`lead_outcome = fallen`)
- leads descartados (`lead_outcome = discarded`)
- conversaciones activas (`lead_outcome = active`)
- detalle de interacciones por conversación

### Query params más usados

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `from_date` | datetime ISO | Inicio del rango por `conversation.started_at` |
| `to_date` | datetime ISO | Fin del rango por `conversation.started_at` |
| `plataforma` | string | `facebook`, `instagram`, `whatsapp` |
| `lead_outcome` | string | `all`, `sale`, `fallen`, `discarded`, `active`, `unknown` |
| `order_status` | string | `draft`, `collecting`, `ready_to_confirm`, `confirmed`, `cancelled`, `closed`, `none` |
| `categoria` | string | `lead`, `proveedor`, `saludo`, `spam`, `auto_reply`, `human_reply` |
| `handoff_state` | string | `auto` o `human_locked` |
| `sender_id` | string | Filtra por remitente exacto |
| `search` | string | Busca por `sender_id`, nombre, teléfono o ciudad |
| `include_interactions` | bool | Incluye mensajes por conversación |
| `max_interactions` | int | Máximo de mensajes por conversación (1-200) |
| `limit` | int | Paginación (1-200) |
| `offset` | int | Paginación desde cero |

### Ejemplo

```http
GET http://localhost:8000/reports/leads?from_date=2026-01-01T00:00:00Z&to_date=2026-12-31T23:59:59Z&plataforma=facebook&lead_outcome=all&include_interactions=true&max_interactions=20&limit=50&offset=0
```

### curl

```bash
curl "http://localhost:8000/reports/leads?plataforma=facebook&lead_outcome=sale&include_interactions=true&max_interactions=20&limit=50&offset=0"
```

---

## POST `/respond` — Enviar respuesta automática

Genera el mensaje según datos **faltantes** en la sesión y lo envía vía Meta Graph API.

### Request — usando sesión existente

Copia el `session_id` de un webhook o de `POST /session`:

```http
POST http://localhost:8000/respond
Content-Type: application/json

{
  "sender_id": "123456789",
  "plataforma": "whatsapp",
  "session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
}
```

### Request — mensaje manual (sin sesión)

```json
{
  "sender_id": "123456789",
  "plataforma": "whatsapp",
  "texto": "¡Listo! En un momento te contactamos."
}
```

### Respuesta `200` — faltan datos

```json
{
  "mensaje": "¡Claro! ¿Cuántos kilos de nopal necesitas aproximadamente?",
  "enviado": false,
  "campos_faltantes": ["cantidad", "telefono"]
}
```

`enviado: false` si `META_PAGE_ACCESS_TOKEN` no está configurado o falló el envío a Meta.

### Respuesta `200` — datos completos

```json
{
  "mensaje": "¡Listo! En un momento te contactamos.",
  "enviado": true,
  "campos_faltantes": []
}
```

### Lógica de qué pregunta

| Falta | Mensaje automático |
|-------|-------------------|
| `cantidad` | "¡Claro! ¿Cuántos kilos de nopal necesitas aproximadamente?" |
| `ciudad` | "Perfecto, ¿de qué ciudad nos escribes?" |
| `telefono` | "Genial, ¿me compartes un número de contacto para darte seguimiento?" |
| Nada | "¡Listo! En un momento te contactamos." |

### curl

```bash
curl -X POST http://localhost:8000/respond \
  -H "Content-Type: application/json" \
  -d "{\"sender_id\":\"123456789\",\"plataforma\":\"whatsapp\",\"session_id\":\"TU-SESSION-UUID\"}"
```

---

# Flujos completos de prueba

## Flujo 1 — Verificar que todo está listo

Orden en Postman:

```
1. GET  /health
2. GET  /webhook/meta   (verificación)
```

**Éxito si:** `/health` → `{"status":"ok"}` y `/webhook/meta` → devuelve el mismo `hub.challenge`.

---

## Flujo 2 — Probar piezas por separado

Ideal para depurar sin tocar la BD de forma compleja:

```
1. POST /extract     → ver qué detecta del texto
2. POST /classify    → ver categoría
3. POST /session     → crear sesión de prueba
```

Ejemplo: el extract detecta ciudad pero no kilos → luego el webhook debería preguntar por kilos.

---

## Flujo 3 — Conversación de lead completa (recomendado)

Simula un cliente que escribe por WhatsApp en **3 mensajes** con el **mismo** `sender_id`.

### Mensaje 1 — Interés + ciudad

```json
POST /webhook/meta
{
  "sender_id": "123456789",
  "texto": "Hola, ¿cuánto el kilo de nopal en Puebla?",
  "plataforma": "whatsapp"
}
```

**Esperado:**
- `tipo`: `"lead"`
- `extraido.ciudad`: `"Puebla"`
- `extraido.cantidad`: `null`
- `respuesta`: pregunta por kilos

**Guarda** `session_id` de la respuesta.

---

### Mensaje 2 — Cantidad

```json
{
  "sender_id": "123456789",
  "texto": "Necesito como 40 kilos",
  "plataforma": "whatsapp"
}
```

**Esperado:**
- `extraido.cantidad`: `"40 kg"`
- `extraido.ciudad`: sigue `"Puebla"` (no se borra)
- `respuesta`: pregunta por teléfono o ciudad si aún falta algo

---

### Mensaje 3 — Teléfono

```json
{
  "sender_id": "123456789",
  "texto": "Mi número es 222 123 4567",
  "plataforma": "whatsapp"
}
```

**Esperado:**
- `extraido.telefono`: `"+522221234567"`
- `respuesta`: `"¡Listo! En un momento te contactamos."`
- `campos_faltantes` implícitos: ninguno

---

### Verificar en base de datos (TablePlus o psql)

Puerto **5433** (Docker; ver `docker-compose.yml`):

```sql
SELECT tipo, mensaje, telefono, cantidad, ciudad, datos_extraidos_en
FROM leads
WHERE session_id = 'TU-SESSION-UUID'
ORDER BY fecha;
```

```sql
SELECT sender_id, telefono, cantidad, ciudad, activa
FROM sessions
WHERE sender_id = '123456789';
```

---

## Flujo 4 — Proveedor (sin respuesta automática)

```json
POST /webhook/meta
{
  "sender_id": "987654321",
  "texto": "Vendo nopal por toneladas, distribuyo en Jalisco",
  "plataforma": "whatsapp"
}
```

**Esperado:**
- `tipo`: `"proveedor"`
- `respuesta`: `null`
- `respuesta_enviada`: `false`

El sistema **no** envía mensaje automático a proveedores.

---

## Flujo 5 — Saludo

```json
{
  "sender_id": "111222333",
  "texto": "Buenos días",
  "plataforma": "facebook"
}
```

**Esperado:** `tipo`: `"saludo"`, sin respuesta automática.

---

## Flujo 6 — Reenviar respuesta manualmente

Útil si en el webhook `respuesta_enviada` fue `false` pero ya tienes tokens de Meta:

```
1. POST /webhook/meta     → obtener session_id
2. POST /respond          → con ese session_id
```

```json
{
  "sender_id": "123456789",
  "plataforma": "whatsapp",
  "session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
}
```

---

## Flujo 7 — Meta real (producción / ngrok)

1. `docker compose up` + `ngrok http 8000`
2. En Meta Developers:
   - **Callback URL:** `https://TU-NGROK.ngrok-free.dev/webhook/meta`
   - **Verify token:** valor de `META_VERIFY_TOKEN`
3. Suscríbete a eventos de mensajes.
4. Escribe desde WhatsApp/Facebook a tu número/página conectada.
5. Revisa logs: `docker compose logs -f api`
6. Revisa BD: tabla `leads`.

---

## Tablas en PostgreSQL

| Tabla | Contenido |
|-------|-----------|
| `sessions` | Estado por usuario (`sender_id` + `plataforma`), datos acumulados |
| `leads` | Cada mensaje procesado con su clasificación |
| `processed_events` | IDs ya procesados (idempotencia) |

---

## Errores frecuentes

| Síntoma | Solución |
|---------|----------|
| `403` en GET `/webhook/meta` | Token distinto entre Postman y `.env`; recrea contenedor API |
| `500` en verificación | Ya corregido: devuelve challenge como texto, no `int()` |
| `respuesta_enviada: false` | Configura `META_PAGE_ACCESS_TOKEN` y `WHATSAPP_PHONE_NUMBER_ID` |
| Postman OK pero Meta falla | ngrok apagado, URL desactualizada o API no accesible |
| No conecta TablePlus | Usa puerto **5433**, user/pass `postgres` / `postgres` |

---

## Orden sugerido en Postman (colección)

1. Verificación Meta (GET)  
2. Crear o recuperar sesión  
3. Extraer datos  
4. Clasificar texto  
5. Simular mensaje entrante (POST) — repetir para flujo multi-mensaje  
6. Enviar respuesta  

Documentación de instalación y Meta: ver [QUICKSTART.md](./QUICKSTART.md).
