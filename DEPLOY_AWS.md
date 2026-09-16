# Guia de despliegue AWS (develop -> produccion)

Esta guia describe como publicar Nopal Leads en AWS primero en develop y luego en produccion, usando contenedores Docker.

Objetivo:
- Entorno develop remoto para validar cambios de rama develop.
- Entorno produccion remoto para trafico real.
- Flujo de promocion controlado de develop a produccion.

## 1. Arquitectura recomendada

Usar stack administrado:
- ECR para imagenes Docker.
- ECS Fargate para correr contenedores.
- RDS PostgreSQL para base de datos.
- ALB + ACM para HTTPS.
- Route 53 para DNS.
- Secrets Manager (o SSM) para secretos.
- CloudWatch para logs y alertas.

Separar todo por entorno:
- develop: recursos propios, secretos propios, base propia.
- produccion: recursos propios, secretos propios, base propia.

## 2. Prerrequisitos

En local:
- AWS CLI configurado con credenciales e IAM correcto.
- Docker instalado.
- Acceso al dominio (si usaras Route 53/ACM publico).

En AWS:
- Cuenta con permisos para ECS, ECR, RDS, IAM, ALB, Route53, Secrets.

En repo:
- Dockerfile funcional.
- Variables de entorno listas en .env.example.
- Script SQL inicial en app/db/migrations.sql.

## 3. Variables de entorno minimas

Para ambos entornos necesitas definir al menos:
- META_VERIFY_TOKEN
- META_APP_SECRET
- META_PAGE_ACCESS_TOKEN
- WHATSAPP_PHONE_NUMBER_ID
- OPENAI_API_KEY
- OPENAI_MODEL
- DATABASE_URL
- JWT_SECRET_KEY
- JWT_ALGORITHM
- JWT_ACCESS_TOKEN_EXPIRE_MINUTES
- SUPERUSER_USERNAME
- SUPERUSER_PASSWORD
- SESSION_IDLE_TIMEOUT_MINUTES
- SESSION_MAX_DURATION_MINUTES
- MESSAGE_BURST_INACTIVITY_SECONDS
- MESSAGE_BURST_MAX_WAIT_SECONDS
- MESSAGE_BURST_MAX_MESSAGES
- HANDOFF_IDLE_TIMEOUT_MINUTES
- BOT_OUTBOUND_EVENT_TTL_SECONDS

Recomendacion:
- Guardar secretos en Secrets Manager.
- No subir .env real a git.

## 4. Paso a paso para develop

### 4.1 Crear repositorio ECR

Ejemplo (ajusta region):

aws ecr create-repository --repository-name alobello-api --region us-east-1

### 4.2 Construir y publicar imagen

Definir variables locales:
- ACCOUNT_ID
- REGION

Ejemplo:

aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

docker build -t alobello-api:develop .

docker tag alobello-api:develop ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/alobello-api:develop

docker push ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/alobello-api:develop

### 4.3 Crear base RDS para develop

Config minima sugerida:
- Motor: PostgreSQL 15.
- Public access: No.
- Security Group: permitir 5432 solo desde ECS tasks SG.
- Backups: activados (aunque sea retencion corta en develop).

### 4.4 Ejecutar migracion inicial

Aplicar app/db/migrations.sql contra RDS develop:

psql "host=RDS_DEV_ENDPOINT port=5432 dbname=nopal_leads user=DB_USER password=DB_PASS sslmode=require" -f app/db/migrations.sql

Nota:
- Repetir este paso en cada nuevo entorno.

### 4.5 Crear Task Definition (ECS Fargate)

Container settings:
- image: ECR_URI/alobello-api:develop
- portMappings: 8000
- command: default del Dockerfile
- cpu/memory: por ejemplo 512/1024 (ajustable)
- logs: awslogs a CloudWatch

Environment/Secrets:
- DATABASE_URL apuntando a RDS develop.
- Resto de variables desde Secrets Manager/SSM.

### 4.6 Crear ECS Service develop

- Cluster: alobello-develop
- Desired count: 1
- Launch type: Fargate
- Network: subnets privadas + NAT (recomendado)
- Security Group de task: salida a internet y acceso a RDS
- Adjuntar a ALB target group

Health check ALB:
- Path: /health
- Expected: 200

### 4.7 Publicar endpoint develop

- Listener HTTPS en ALB (cert ACM).
- DNS en Route 53, por ejemplo:
  - api-dev.tudominio.com -> ALB

### 4.8 Pruebas de humo develop

Validar en ese orden:
1. GET /health
2. GET /docs
3. GET /
4. POST /auth/login
5. GET /reports/leads con Bearer token
6. GET /dashboard en navegador

## 5. Paso a paso para produccion

La idea es replicar la receta de develop, pero aislando recursos.

### 5.1 Crear recursos separados de produccion

- Cluster ECS prod independiente.
- RDS prod independiente (ideal Multi-AZ).
- Secrets prod independientes.
- ALB prod (o listeners/target groups separados).
- DNS prod, por ejemplo api.tudominio.com.

### 5.2 Publicar imagen de release

Tag sugerido:
- alobello-api:prod-YYYYMMDD-HHMM
- o alobello-api:gitsha

Ejemplo:

docker build -t alobello-api:prod-20260914-1 .

docker tag alobello-api:prod-20260914-1 ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/alobello-api:prod-20260914-1

docker push ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/alobello-api:prod-20260914-1

### 5.3 Migracion en RDS produccion

Ejecutar app/db/migrations.sql sobre la base prod.

Antes:
- Snapshot/backup.

Despues:
- Verificar tablas y permisos.

### 5.4 Actualizar ECS Service prod

- Cambiar Task Definition a nueva imagen.
- Hacer rolling deploy.
- Monitorear health checks y logs.

### 5.5 Validacion post deploy prod

1. /health responde 200
2. /docs disponible
3. Login JWT funcional
4. Dashboard carga datos
5. Reporte responde con filtros
6. Webhook de Meta verifica callback

## 6. Flujo de ramas recomendado

- develop:
  - Merge de features.
  - Deploy automatico a entorno develop.

- main:
  - Solo entra codigo validado en develop.
  - Deploy a produccion con aprobacion manual.

Regla simple:
- develop prueba integracion.
- main publica estable.

## 7. CI/CD sugerido (GitHub Actions)

Pipeline develop:
1. Trigger en push a develop.
2. Build Docker.
3. Push a ECR con tag develop + sha.
4. Update ECS develop service.

Pipeline produccion:
1. Trigger en push/tag de main.
2. Build y push imagen release.
3. Gate de aprobacion manual.
4. Update ECS produccion service.
5. Smoke tests basicos.

## 8. Seguridad minima obligatoria

- HTTPS obligatorio (ACM + ALB).
- RDS no publico.
- Secrets fuera de git.
- JWT_SECRET_KEY robusta y rotada.
- SUPERUSER_PASSWORD robusta y rotada.
- Principio de minimo privilegio IAM.
- Security Groups cerrados por origen.

## 9. Operacion y observabilidad

CloudWatch Logs:
- Centralizar logs de api.
- Agregar metric filters para errores.

Alarmas recomendadas:
- ALB 5XX > umbral.
- Latencia p95 alta.
- ECS CPU/Mem alta.
- Reinicios de task.

## 10. Rollback rapido

Si un deploy falla:
1. Volver a la Task Definition anterior en ECS.
2. Confirmar target healthy en ALB.
3. Revisar logs y causa raiz.
4. Corregir en develop antes de reintentar prod.

## 11. Checklist de salida a remoto

Develop listo cuando:
- Endpoint HTTPS develop estable.
- Health, login, dashboard y reporte OK.
- Webhook de prueba OK.

Produccion lista cuando:
- Endpoint HTTPS prod estable.
- Monitoreo y alarmas activos.
- Backups RDS validados.
- Runbook de rollback probado.

---

## Anexo A: opcion rapida (menos recomendada)

Puedes usar EC2 + Docker Compose para salir rapido:
- 1 instancia EC2 para api.
- RDS aparte para postgres.
- Nginx + certbot para HTTPS.

Ventaja:
- Mas simple al inicio.

Desventaja:
- Menos elasticidad y mas mantenimiento manual que ECS Fargate.

Para un proyecto que crecera, conviene arrancar con ECS Fargate.
