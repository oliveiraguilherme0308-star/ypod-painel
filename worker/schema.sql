-- Banco D1 do YPOD (o mesmo SQLite de sempre, so que na Cloudflare).
--
-- Aplicar:   wrangler d1 execute db-ypod_sm --remote --file=schema.sql
-- Em teste:  wrangler d1 execute db-ypod_sm --local  --file=schema.sql
--
-- As colunas de sensor sao as mesmas de KEYS em ypod_monitor.py e em
-- src/index.js. Sensor novo = ALTER TABLE aqui + a chave nos dois arquivos.

-- Uma linha por media de minuto, como chega do PC. "k" e o src_key do banco
-- local ("min:<epoch>"): reenviar o mesmo minuto nao duplica (INSERT OR IGNORE).
CREATE TABLE IF NOT EXISTS leituras (
  k         TEXT PRIMARY KEY,
  ts        REAL NOT NULL,          -- epoch em segundos (meio do minuto)
  pod_id    TEXT,
  firmware  TEXT,
  temp REAL, rh REAL, fig2600 REAL, fig2602 REAL, mox3 REAL, co REAL, aux REAL,
  co2 REAL, pm1 REAL, pm25 REAL, pm10 REAL, vento_vel REAL, vento_dir REAL
);
CREATE INDEX IF NOT EXISTS idx_leituras_ts ON leituras(ts);

-- Resumo por hora. O plano gratis do D1 cobra por linha LIDA: 30 dias por
-- minuto sao 43 mil linhas por consulta; por hora, 720. Janelas acima de 2
-- dias leem daqui. Refeito pelo Worker a cada lote recebido.
CREATE TABLE IF NOT EXISTS hora (
  h INTEGER PRIMARY KEY,            -- epoch / 3600
  n INTEGER,
  temp_s REAL, temp_n INTEGER, temp_lo REAL, temp_hi REAL,
  rh_s REAL, rh_n INTEGER, rh_lo REAL, rh_hi REAL,
  fig2600_s REAL, fig2600_n INTEGER, fig2600_lo REAL, fig2600_hi REAL,
  fig2602_s REAL, fig2602_n INTEGER, fig2602_lo REAL, fig2602_hi REAL,
  mox3_s REAL, mox3_n INTEGER, mox3_lo REAL, mox3_hi REAL,
  co_s REAL, co_n INTEGER, co_lo REAL, co_hi REAL,
  aux_s REAL, aux_n INTEGER, aux_lo REAL, aux_hi REAL,
  co2_s REAL, co2_n INTEGER, co2_lo REAL, co2_hi REAL,
  pm1_s REAL, pm1_n INTEGER, pm1_lo REAL, pm1_hi REAL,
  pm25_s REAL, pm25_n INTEGER, pm25_lo REAL, pm25_hi REAL,
  pm10_s REAL, pm10_n INTEGER, pm10_lo REAL, pm10_hi REAL,
  vento_vel_s REAL, vento_vel_n INTEGER, vento_vel_lo REAL, vento_vel_hi REAL,
  vento_dir_s REAL, vento_dir_n INTEGER, vento_dir_lo REAL, vento_dir_hi REAL,
  -- Direcao e grandeza CIRCULAR: somar graus e tirar a media da 180 graus
  -- entre 350 e 10, a direcao oposta, e o erro aparece justamente com vento de
  -- norte. Guardamos a soma dos senos e dos cossenos; o angulo do balde sai de
  -- atan2(soma_sen, soma_cos). As colunas _s/_lo/_hi de vento_dir ficam sem
  -- uso, mantidas so para nao quebrar bancos ja criados.
  vento_dir_sen REAL, vento_dir_cos REAL
);

-- Linha unica: contadores (COUNT(*) a cada visita custaria a tabela inteira
-- em linhas lidas) e o ultimo retrato "ao vivo" mandado pelo PC.
CREATE TABLE IF NOT EXISTS estado (
  id       INTEGER PRIMARY KEY CHECK (id = 1),
  first    REAL,
  last     REAL,
  rows     INTEGER NOT NULL DEFAULT 0,
  live     TEXT,                    -- JSON igual ao /api/live do PC
  live_at  INTEGER                  -- quando chegou (ms, relogio da Cloudflare)
);
INSERT OR IGNORE INTO estado (id, rows) VALUES (1, 0);
