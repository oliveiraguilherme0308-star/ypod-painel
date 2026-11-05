-- Alinha as colunas do D1 com o KEYS de ypod_monitor.py.
--
-- Aplicar:  wrangler d1 execute db-ypod_sm --remote --file=migracao-2026-10-30-campos.sql
--
-- O QUE ESTAVA ERRADO
--
-- Em 25/10 o mapa de campos do servidor foi corrigido lendo o firmware do pod:
-- o campo 12 nao era "MOx 3", e ozonio ("o3"), e o 15 nao era "Aux / O3", e o
-- segundo eletrodo do mesmo sensor de CO ("co_ae"). Ao mesmo tempo entraram duas
-- chaves que antes nao existiam: "tvoc" (COV) e "co_we" (eletrodo ativo).
--
-- O schema.sql e o src/index.js nao acompanharam. Como o push e lido por NOME
-- (json_extract '$.<chave>'), nada falhou: o PC mandava tvoc/o3/co_we/co_ae, o
-- Worker procurava mox3/aux, e os quatro valores eram descartados sem erro
-- nenhum. O /api/live continuou certo porque e retrato repassado do PC, e nao
-- passa por estas colunas -- por isso a tela mostrava o dado e o historico nao.
--
-- RENAME em vez de DROP + ADD: a coluna renomeada leva junto o que ja estava
-- gravado nela, que e dado bom com nome errado.

-- Uma linha por media de minuto ------------------------------------------------
ALTER TABLE leituras RENAME COLUMN mox3 TO o3;
ALTER TABLE leituras RENAME COLUMN aux  TO co_ae;
ALTER TABLE leituras ADD COLUMN tvoc  REAL;
ALTER TABLE leituras ADD COLUMN co_we REAL;

-- Resumo por hora --------------------------------------------------------------
ALTER TABLE hora RENAME COLUMN mox3_s  TO o3_s;
ALTER TABLE hora RENAME COLUMN mox3_n  TO o3_n;
ALTER TABLE hora RENAME COLUMN mox3_lo TO o3_lo;
ALTER TABLE hora RENAME COLUMN mox3_hi TO o3_hi;

ALTER TABLE hora RENAME COLUMN aux_s  TO co_ae_s;
ALTER TABLE hora RENAME COLUMN aux_n  TO co_ae_n;
ALTER TABLE hora RENAME COLUMN aux_lo TO co_ae_lo;
ALTER TABLE hora RENAME COLUMN aux_hi TO co_ae_hi;

ALTER TABLE hora ADD COLUMN tvoc_s  REAL;
ALTER TABLE hora ADD COLUMN tvoc_n  INTEGER;
ALTER TABLE hora ADD COLUMN tvoc_lo REAL;
ALTER TABLE hora ADD COLUMN tvoc_hi REAL;

ALTER TABLE hora ADD COLUMN co_we_s  REAL;
ALTER TABLE hora ADD COLUMN co_we_n  INTEGER;
ALTER TABLE hora ADD COLUMN co_we_lo REAL;
ALTER TABLE hora ADD COLUMN co_we_hi REAL;

-- A tabela "hora" e refeita pelo Worker a cada lote recebido (refazerHoras), e
-- as horas que ja existem sao reescritas quando chega minuto novo da mesma hora.
-- Entao nao ha backfill a fazer aqui: as quatro colunas novas se preenchem
-- sozinhas conforme o PC envia. O que nao volta e o passado em que o PC mandou e
-- o Worker descartou -- esse dado so existe no SQLite do PC da escola.
