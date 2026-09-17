# Guia AWS para primer despliegue (sin experiencia previa en nube)

Esta guia es para ti si programas y ya sabes construir aplicaciones, pero nunca has configurado AWS como responsable de infraestructura.

Objetivo:
- Dejar una cuenta AWS lista y segura.
- Entender que servicio usar para cada parte de tu app.
- Hacer tu primer despliegue en entorno develop con bajo riesgo.
- Quedar listo para pasar a produccion despues.

Esta guia complementa la guia operativa de despliegue que ya existe en el repo.

## 1) Mapa mental rapido

Piensa en AWS en 4 capas:
1. Cuenta y seguridad: quien puede entrar y con que permisos.
2. Red y acceso: por donde entra trafico y quien puede hablar con quien.
3. Runtime de tu app: donde corre el contenedor.
4. Datos y secretos: base de datos y claves sensibles.

Para este proyecto, el stack recomendado es:
- ECS Fargate: ejecutar API sin administrar servidores.
- ECR: guardar imagen Docker.
- RDS PostgreSQL: base de datos administrada.
- ALB + ACM: HTTPS y balanceo.
- Route 53: DNS.
- Secrets Manager: secretos.
- CloudWatch: logs y alertas.

## 2) Antes de tocar infraestructura

Define estas decisiones una sola vez:
1. Region principal. Ejemplo: us-east-1.
2. Nombre base del proyecto. Ejemplo: alobello.
3. Ambientes iniciales: develop y production.
4. Presupuesto mensual maximo para no llevarte sorpresas.

## 3) Paso cero obligatorio: seguridad de cuenta

### 3.1 Crea la cuenta AWS
1. Crea cuenta con correo dedicado del proyecto (no personal).
2. Configura metodo de pago.

### 3.2 Endurece usuario root
1. Activa MFA en root.
2. No uses root para trabajar dia a dia.
3. Guarda credenciales root en lugar seguro.

### 3.3 Crea un usuario administrador de trabajo
1. En IAM crea un grupo de administradores.
2. Crea usuario IAM para ti.
3. Asigna MFA al usuario IAM.
4. Usa ese usuario para consola y CLI.

### 3.4 Activa alertas de facturacion
1. Activa AWS Budgets.
2. Crea alertas a 50%, 80% y 100% del presupuesto mensual.
3. Configura alertas por email.

## 4) Herramientas locales minimas

Instala:
1. AWS CLI v2
2. Docker
3. psql client (para correr migraciones)

Configura AWS CLI con tu perfil:

```bash
aws configure
```

Completa:
- Access Key ID
- Secret Access Key
- Default region
- Output format (json)

Verifica:

```bash
aws sts get-caller-identity
```

Si este comando responde, ya puedes operar tu cuenta por CLI.

## 5) Estructura por ambientes (regla de oro)

No mezcles develop y production.

Como minimo separa:
1. Cluster ECS
2. Base RDS
3. Secretos
4. DNS
5. Alarmas

Convencion simple sugerida:
- alobello-dev-*
- alobello-prod-*

## 6) Red sin dolor (version inicial segura)

Si es tu primer despliegue real:
1. Usa VPC dedicada para el proyecto.
2. Crea subredes privadas para ECS y RDS.
3. Crea subredes publicas solo para ALB.
4. RDS sin acceso publico.
5. Security Groups estrictos:
   - ALB acepta 443 desde internet.
   - ECS acepta 8000 solo desde ALB.
   - RDS acepta 5432 solo desde ECS.

Si esto te suena complejo, puedes arrancar con ayuda visual de la consola y luego refinar.

## 7) Secretos y variables

No pongas secretos en repositorio ni en imagen Docker.

Guarda en Secrets Manager:
- OPENAI_API_KEY
- META_PAGE_ACCESS_TOKEN
- META_APP_SECRET
- JWT_SECRET_KEY
- SUPERUSER_PASSWORD

Variables no sensibles pueden ir como environment variables en task definition.

## 8) Primer deploy en develop (camino recomendado)

### 8.1 Crea repositorio ECR

```bash
aws ecr create-repository --repository-name alobello-api --region us-east-1
```

### 8.2 Build y push de imagen

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com
docker build -t alobello-api:develop .
docker tag alobello-api:develop ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/alobello-api:develop
docker push ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/alobello-api:develop
```

### 8.3 Crea RDS PostgreSQL (develop)
1. Engine: PostgreSQL 15.
2. Public access: No.
3. Security Group: permitir 5432 solo desde ECS.

### 8.4 Ejecuta migraciones

```bash
psql "host=RDS_DEV_ENDPOINT port=5432 dbname=nopal_leads user=DB_USER password=DB_PASS sslmode=require" -f app/db/migrations.sql
```

### 8.5 Crea task definition ECS
Parametros base:
1. Imagen de ECR.
2. Puerto 8000.
3. CPU/memoria inicial: 512/1024.
4. Logs a CloudWatch.
5. Secrets + env vars.

### 8.6 Crea ECS Service
1. Desired count: 1.
2. Attach a ALB target group.
3. Health check path: /health.

### 8.7 HTTPS y dominio
1. Solicita certificado en ACM.
2. Listener 443 en ALB.
3. Registra api-dev.tudominio.com en Route 53 hacia ALB.

## 9) Smoke tests obligatorios

Despues de deploy, valida en este orden:
1. GET /health
2. GET /docs
3. POST /auth/login
4. GET /reports/leads con token
5. GET /dashboard

Si uno falla, no avances a produccion.

## 10) Observabilidad minima para no volar a ciegas

En CloudWatch:
1. Logs centralizados del contenedor API.
2. Alarma de 5XX del ALB.
3. Alarma de CPU alta en ECS.
4. Alarma de conexiones o storage de RDS.

## 11) Costos: lo que mas pega al inicio

Normalmente el costo inicial se concentra en:
1. RDS (instancia + almacenamiento)
2. NAT Gateway (si lo usas)
3. ALB
4. Tráfico saliente

Acciones practicas:
1. Empieza con tamanos pequenos en develop.
2. Apaga entornos de prueba cuando no se usan.
3. Revisa costos semanalmente el primer mes.

## 12) Ruta a produccion sin drama

Cuando develop este estable:
1. Replica la arquitectura en prod (aislada).
2. Crea secretos propios de prod.
3. Corre migraciones en RDS prod.
4. Haz deploy con tag versionado.
5. Monitorea 30-60 minutos post deploy.

## 13) Checklist final de cuenta bien configurada

- Root con MFA y sin uso operativo.
- Usuario IAM de trabajo con MFA.
- Budgets con alertas por email.
- ECR, ECS, RDS, ALB y Route 53 creados por ambiente.
- RDS privado.
- Secrets fuera de git.
- CloudWatch con logs y alarmas basicas.
- Runbook de rollback definido.

## 14) Siguiente paso recomendado en este repo

Despues de terminar esta guia, usa la guia operativa de despliegue del proyecto para ejecutar el flujo develop -> produccion con comandos concretos.