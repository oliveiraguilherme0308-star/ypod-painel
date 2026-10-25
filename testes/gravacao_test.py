"""
Gravacao por minuto, tela em tempo real.

O requisito: o banco guarda um registro por minuto (a media), mas os valores na
tela continuam acompanhando cada leitura. Este teste separa as duas coisas e
confere que a media gravada bate com a media real das leituras do minuto.
"""
import os, shutil, sys, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ypod_monitor as y

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_grav")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
y.DATA_DIR = TMP
y.DB = y.Db(os.path.join(TMP, "ypod.db"))
y.ACUM = y.AcumuladorPorMinuto(60)


def linha(quando, temp, co2):
    return ("%s,,,YPODV6,YPOD_V4.2.1,,,%.2f,61.00,,2500,230,65535,,3800,2900,"
            "%.2f,4,6,6," % (quando.strftime("%Y-%m-%dT%H:%M:%S"), temp, co2))


# --- 10 minutos de leituras a cada 2,5 s --------------------------------
base = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=10)
temps_por_minuto = {}
n_leituras = 0
t = base
fim = base + timedelta(minutes=10)
while t < fim:
    # temperatura sobe devagar e oscila, para a media do minuto ser diferente
    # de qualquer leitura isolada
    temp = 22.0 + (t - base).total_seconds() / 600.0 + (0.4 if n_leituras % 2 else -0.4)
    co2 = 700 + (t - base).total_seconds() / 10.0
    minuto = int(t.timestamp() // 60) * 60
    temps_por_minuto.setdefault(minuto, []).append(temp)
    y.ingest(t.timestamp(), linha(t, temp, co2))
    n_leituras += 1
    t += timedelta(seconds=2.5)

for ts_b, medias, m, ln, n, ini in y.ACUM.fechar_tudo():
    m = dict(m); m["amostras"] = n
    y.DB.insert(ts_b, medias, m, ln, "min:%d" % ini)

print("[ok] %d leituras entregues ao servidor (uma a cada 2,5 s)" % n_leituras)

linhas_banco = y.DB.conn().execute("SELECT COUNT(*) FROM readings").fetchone()[0]
assert linhas_banco == 10, "esperava 10 registros (1/min), veio %d" % linhas_banco
print("[ok] banco tem %d registros: um por minuto, nao %d" % (linhas_banco, n_leituras))
print("     reducao de %.0fx no volume" % (n_leituras / linhas_banco))

# --- a media gravada bate com a media real? -----------------------------
piores = []
for minuto, temps in sorted(temps_por_minuto.items()):
    esperada = sum(temps) / len(temps)
    row = y.DB.conn().execute(
        "SELECT temp FROM readings WHERE ts >= ? AND ts < ?",
        (minuto, minuto + 60)).fetchone()
    assert row, "minuto %s nao foi gravado" % minuto
    piores.append(abs(row[0] - esperada))
assert max(piores) < 0.001, "media gravada nao bate: erro de %.4f" % max(piores)
print("[ok] a media gravada bate com a media real das leituras (erro < 0,001 C)")

# --- a tela continua vendo leitura a leitura ----------------------------
raw, latest, ts, meta = y.LIVE.snapshot()
assert len(raw) > 200, "a memoria tem so %d leituras" % len(raw)
assert y.LIVE.count == n_leituras, (y.LIVE.count, n_leituras)
print("[ok] memoria (o que a tela mostra): %d leituras, %d na janela viva"
      % (y.LIVE.count, len(raw)))
print("     valor atual na tela: %.2f C  <- leitura individual, nao media"
      % latest["temp"])

ultimo_min = y.DB.conn().execute(
    "SELECT temp FROM readings ORDER BY ts DESC LIMIT 1").fetchone()[0]
print("     ultimo no banco ...: %.2f C  <- media do minuto" % ultimo_min)
assert abs(latest["temp"] - ultimo_min) > 0.01, \
    "os dois valores coincidiram; o teste nao provou a diferenca"
print("[ok] os dois sao diferentes, como esperado")

# --- amostras por registro ----------------------------------------------
n_amostras = y.DB.conn().execute(
    "SELECT pod_id FROM readings LIMIT 1").fetchone()
cols = {r[1] for r in y.DB.conn().execute("PRAGMA table_info(readings)")}
print("[ok] colunas do banco: %d sensores + metadados" % len(y.KEYS))

# --- reenviar o mesmo periodo nao duplica -------------------------------
antes = linhas_banco
y.ACUM = y.AcumuladorPorMinuto(60)
t = base
while t < base + timedelta(minutes=3):
    temp = 22.0 + (t - base).total_seconds() / 600.0
    y.ingest(t.timestamp(), linha(t, temp, 700))
    t += timedelta(seconds=2.5)
for ts_b, medias, m, ln, n, ini in y.ACUM.fechar_tudo():
    m = dict(m); m["amostras"] = n
    y.DB.insert(ts_b, medias, m, ln, "min:%d" % ini)
depois = y.DB.conn().execute("SELECT COUNT(*) FROM readings").fetchone()[0]
assert depois == antes, "reenvio criou linha nova: %d -> %d" % (antes, depois)
print("[ok] reenviar o mesmo periodo nao duplica (chave do minuto)")

# --- graficos e consultas continuam funcionando -------------------------
agora = time.time()
serie, balde = y.DB.series(agora - 3600, agora + 60)
stats = y.DB.stats(agora - 3600, agora + 60)
assert serie["temp"], "serie vazia"
print("[ok] serie para o grafico: %d pontos | stats temp: %s"
      % (len(serie["temp"]), stats["temp"]))

print("\nTUDO PASSOU")
