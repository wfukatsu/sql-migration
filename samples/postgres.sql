-- PostgreSQL sample
CREATE TABLE users (
  id SERIAL PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  name TEXT,
  balance NUMERIC(12,2) DEFAULT 0,
  active BOOLEAN DEFAULT true,
  meta JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE events (
  user_id BIGINT NOT NULL,
  occurred_at TIMESTAMP NOT NULL,
  kind TEXT,
  payload BYTEA,
  PRIMARY KEY (user_id, occurred_at)
);
CREATE INDEX idx_users_email ON users (email);
CREATE INDEX idx_events_kind_ts ON events (kind, occurred_at);
SELECT id, name FROM users WHERE id = $1;
SELECT id, name FROM users WHERE email ILIKE '%@example.com' LIMIT 10 OFFSET 20;
SELECT user_id, occurred_at FROM events WHERE user_id = 42 AND occurred_at >= '2024-01-01' ORDER BY occurred_at DESC LIMIT 100;
SELECT u.name, e.kind FROM users u JOIN events e ON u.id = e.user_id WHERE u.active = true;
SELECT kind, COUNT(*) AS n FROM events GROUP BY kind ORDER BY n DESC;
SELECT name, UPPER(name) FROM users WHERE id = 1;
WITH recent AS (SELECT * FROM events WHERE occurred_at > now() - interval '1 day') SELECT COUNT(*) FROM recent;
SELECT * FROM users WHERE (active = true OR balance > 100) AND NOT (name IS NULL);
INSERT INTO users (id, email, name) VALUES (1, 'a@example.com', 'A');
INSERT INTO users (id, email, name) VALUES (1, 'a@example.com', 'A') ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, name = EXCLUDED.name;
INSERT INTO users (id, email, name) VALUES (1, 'a@example.com', 'A') ON CONFLICT (id) DO NOTHING;
INSERT INTO events (user_id, occurred_at, kind) SELECT id, now(), 'signup' FROM users;
UPDATE users SET balance = balance - 10 WHERE id = 1;
UPDATE users SET name = 'B', active = false WHERE id = 1;
DELETE FROM events WHERE user_id = 42 AND occurred_at < '2023-01-01';
DELETE FROM events USING users WHERE events.user_id = users.id AND users.active = false;
BEGIN;
COMMIT;
