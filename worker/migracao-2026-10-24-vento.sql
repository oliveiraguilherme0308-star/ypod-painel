-- Migracao: media circular da direcao do vento
-- --------------------------------------------
-- Aplicar ANTES de publicar a versao nova do Worker:
--
--   npx wrangler d1 execute db-ypod_sm --remote --file=migracao-2026-10-24-vento.sql
--   npx wrangler deploy
--
-- Nesta ordem. O Worker novo escreve nestas colunas; se ele subir primeiro, o
-- /api/push falha e o PC da escola comeca a tomar erro.
--
-- E aditiva e idempotente no efeito: so acrescenta colunas e recalcula uma
-- tabela derivada. Nenhuma leitura e tocada. Se precisar rodar de novo, as
-- duas primeiras linhas vao reclamar que a coluna ja existe -- e so seguir.

ALTER TABLE hora ADD COLUMN vento_dir_sen REAL;
ALTER TABLE hora ADD COLUMN vento_dir_cos REAL;

-- Preenche as horas que ja existiam. Sem isto, qualquer janela longa ficaria
-- sem direcao ate aquelas horas serem reescritas por um push novo -- o que
-- nunca aconteceria, porque push so mexe nas horas que recebem dado.
UPDATE hora SET
  vento_dir_sen = (SELECT SUM(sin(radians(vento_dir))) FROM leituras
                   WHERE CAST(ts / 3600 AS INTEGER) = hora.h),
  vento_dir_cos = (SELECT SUM(cos(radians(vento_dir))) FROM leituras
                   WHERE CAST(ts / 3600 AS INTEGER) = hora.h);
