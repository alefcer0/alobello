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
| `POSTGRES_*` / `DATABASE_URL` | Valores por defecto del `.env.example` funcionan con Docker Compose |

## 3. Levantar el proyecto

```bash
docker compose up --build
```

- La API queda en **http://localhost:8000**
- Documentación interactiva: **http://localhost:8000/docs**
- Health check: **http://localhost:8000/health**
- PostgreSQL ejecuta `app/db/migrations.sql` automáticamente al crear el volumen por primera vez

## 4. Configurar el webhook en Meta

Meta requiere una **URL pública HTTPS** para enviar eventos.

### Desarrollo local con ngrok

```bash
ngrok http 8000
```

Copia la URL HTTPS que ngrok muestra (ej. `https://a1b2c3d4.ngrok-free.app`) y en Meta Developers:

1. Ve a tu app → **Webhooks** (o WhatsApp → Configuration → Webhook)
2. **Callback URL:** `https://TU-DOMINIO-NGROK/webhook/meta`
3. **Verify token:** el mismo valor que `META_VERIFY_TOKEN` en tu `.env`
4. Suscríbete a los campos: `messages`, `messaging`, `comments` (según plataforma)

En producción, coloca Nginx (u otro proxy) con TLS delante del contenedor `api` en el puerto 8000.

## 5. Importar Postman

1. Abre Postman → **Import**
2. Importa `nopal_leads.postman_collection.json` y `nopal_leads.postman_environment.json`
3. Selecciona el ambiente **nopal_leads** en el selector superior derecho
4. Ajusta `meta_verify_token` en el ambiente para que coincida con tu `.env`

### Orden recomendado de pruebas

1. **Verificación Meta (GET)** — confirma que el token de verificación funciona
2. **Crear o recuperar sesión** — crea la sesión del usuario de prueba
3. **Extraer datos** — prueba la extracción aislada
4. **Clasificar texto** — prueba reglas y (opcional) OpenAI
5. **Simular mensaje entrante (POST)** — flujo completo end-to-end
6. **Enviar respuesta** — prueba el envío a Meta (requiere tokens válidos)

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
  "processed": 1,
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
docker compose exec db psql -U postgres -d nopal_leads -c "SELECT tipo, mensaje, telefono, cantidad, ciudad FROM leads ORDER BY fecha DESC LIMIT 5;"
```

```bash
docker compose exec db psql -U postgres -d nopal_leads -c "SELECT sender_id, telefono, cantidad, ciudad, activa FROM sessions;"
```

## Estructura del proyecto

```
app/
  main.py
  routes/
    webhook.py
    internal.py
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
```

## Notas

- Las reglas por keywords se evalúan **antes** de llamar a OpenAI para minimizar costo y latencia
- Los eventos duplicados (mismo `event_id`) se ignoran automáticamente
- El webhook POST tiene rate limit de **60 requests/minuto** por IP
- En producción, usa siempre HTTPS detrás de un reverse proxy (Nginx recomendado)
