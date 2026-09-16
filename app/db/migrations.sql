CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS contacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre VARCHAR(255),
  telefono VARCHAR(50),
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS contact_identities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  sender_id VARCHAR(255) NOT NULL,
  plataforma VARCHAR(20) NOT NULL,
  source_account_id VARCHAR(255),
  first_seen_at TIMESTAMP DEFAULT NOW(),
  last_seen_at TIMESTAMP DEFAULT NOW(),
  UNIQUE (plataforma, sender_id)
);

CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contact_identity_id UUID NOT NULL REFERENCES contact_identities(id) ON DELETE CASCADE,
  status VARCHAR(20) NOT NULL DEFAULT 'open',
  handoff_state VARCHAR(20) NOT NULL DEFAULT 'auto',
  handoff_source VARCHAR(30),
  handoff_locked_at TIMESTAMP,
  handoff_until TIMESTAMP,
  clarification_attempts INTEGER NOT NULL DEFAULT 0,
  topic_change_count INTEGER NOT NULL DEFAULT 0,
  current_topic VARCHAR(160),
  interaction_closed BOOLEAN NOT NULL DEFAULT FALSE,
  started_at TIMESTAMP DEFAULT NOW(),
  last_message_at TIMESTAMP DEFAULT NOW(),
  closed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  status VARCHAR(20) NOT NULL DEFAULT 'draft',
  intento VARCHAR(20),
  telefono VARCHAR(50),
  cantidad VARCHAR(100),
  ciudad VARCHAR(100),
  confirmed_by_openai BOOLEAN NOT NULL DEFAULT FALSE,
  notas TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  closed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_code VARCHAR(50) NOT NULL DEFAULT 'nopal',
  product_name VARCHAR(120) NOT NULL DEFAULT 'Nopal',
  quantity NUMERIC(12, 3),
  unit VARCHAR(20),
  notes TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  UNIQUE (order_id, product_code)
);

CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  order_id UUID REFERENCES orders(id) ON DELETE SET NULL,
  external_message_id VARCHAR(255),
  direction VARCHAR(10) NOT NULL,
  plataforma VARCHAR(20) NOT NULL,
  sender_id VARCHAR(255) NOT NULL,
  categoria VARCHAR(20),
  intent_category VARCHAR(20),
  detected_topic VARCHAR(160),
  texto TEXT,
  classified_by_openai BOOLEAN NOT NULL DEFAULT FALSE,
  payload JSONB,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth_users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username VARCHAR(120) NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  password_salt TEXT NOT NULL,
  password_iterations INTEGER NOT NULL,
  is_superuser BOOLEAN NOT NULL DEFAULT FALSE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS confirmed_by_openai BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS classified_by_openai BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS intent_category VARCHAR(20);

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS detected_topic VARCHAR(160);

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS handoff_state VARCHAR(20) NOT NULL DEFAULT 'auto';

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS handoff_source VARCHAR(30);

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS handoff_locked_at TIMESTAMP;

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS handoff_until TIMESTAMP;

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS clarification_attempts INTEGER NOT NULL DEFAULT 0;

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS topic_change_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS current_topic VARCHAR(160);

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS interaction_closed BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS processed_events (
  event_id VARCHAR(255) PRIMARY KEY,
  processed_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contact_identities_contact ON contact_identities(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_identities_sender ON contact_identities(sender_id);
CREATE INDEX IF NOT EXISTS idx_conversations_identity_status ON conversations(contact_identity_id, status, last_message_at);
CREATE INDEX IF NOT EXISTS idx_conversations_handoff_until ON conversations(handoff_until);
CREATE INDEX IF NOT EXISTS idx_conversations_interaction_closed ON conversations(interaction_closed);
CREATE INDEX IF NOT EXISTS idx_orders_conversation_status ON orders(conversation_id, status);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_sender_platform ON messages(sender_id, plataforma);
CREATE INDEX IF NOT EXISTS idx_messages_intent_category ON messages(intent_category);
CREATE INDEX IF NOT EXISTS idx_messages_detected_topic ON messages(detected_topic);
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_active_per_conversation
  ON orders(conversation_id)
  WHERE status IN ('draft', 'collecting', 'ready_to_confirm');

-- Normalize legacy unit variants to canonical values.
UPDATE order_items
SET unit = LOWER(unit)
WHERE unit IS NOT NULL;

UPDATE order_items
SET unit = 'kg'
WHERE unit IN ('kgs', 'kilo', 'kilos', 'kilogramo', 'kilogramos');

UPDATE order_items
SET unit = 'tonelada'
WHERE unit IN ('ton', 'tons', 'toneladas');

UPDATE order_items
SET unit = 'caja'
WHERE unit IN ('cajas');

UPDATE order_items
SET unit = 'costal'
WHERE unit IN ('costales');

UPDATE order_items
SET unit = 'bulto'
WHERE unit IN ('bultos');

UPDATE order_items
SET unit = 'manojo'
WHERE unit IN ('manojos');

UPDATE order_items
SET unit = 'penca'
WHERE unit IN ('pencas');

UPDATE order_items
SET unit = 'pieza'
WHERE unit IN ('piezas');

-- Keep unknown historical values as null to satisfy the enum-like check.
UPDATE order_items
SET unit = NULL
WHERE unit IS NOT NULL
  AND unit NOT IN ('kg', 'tonelada', 'caja', 'costal', 'bulto', 'manojo', 'penca', 'pieza');

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_order_items_unit_enum'
  ) THEN
    ALTER TABLE order_items
      ADD CONSTRAINT chk_order_items_unit_enum
      CHECK (
        unit IS NULL
        OR unit IN ('kg', 'tonelada', 'caja', 'costal', 'bulto', 'manojo', 'penca', 'pieza')
      );
  END IF;
END $$;
