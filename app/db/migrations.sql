CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sender_id VARCHAR(255) NOT NULL,
  plataforma VARCHAR(20),
  ultimo_contacto TIMESTAMP DEFAULT NOW(),
  activa BOOLEAN DEFAULT TRUE,
  telefono VARCHAR(50),
  cantidad VARCHAR(100),
  ciudad VARCHAR(100),
  nombre VARCHAR(255),
  UNIQUE (sender_id, plataforma)
);

CREATE TABLE IF NOT EXISTS leads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES sessions(id),
  fecha TIMESTAMP DEFAULT NOW(),
  plataforma VARCHAR(20),
  tipo VARCHAR(20),
  mensaje TEXT,
  nombre VARCHAR(255),
  telefono VARCHAR(50),
  cantidad VARCHAR(100),
  ciudad VARCHAR(100),
  datos_extraidos_en VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS processed_events (
  event_id VARCHAR(255) PRIMARY KEY,
  processed_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_sender ON sessions(sender_id);
CREATE INDEX IF NOT EXISTS idx_sessions_activa ON sessions(activa, ultimo_contacto);
CREATE INDEX IF NOT EXISTS idx_leads_session ON leads(session_id);
