"""
Media de direcao do vento (grandeza circular).

O caso que importa e o vento de NORTE: as leituras ficam oscilando em volta de
zero (355, 358, 2, 5...) e a media aritmetica devolve ~180 graus -- a direcao
oposta. Nao trava, nao avisa, e o numero parece plausivel.

Este teste cobre as tres camadas onde a media acontece: o acumulador que grava,
o resumo por minuto e as estatisticas da janela.
"""
import os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ypod_monitor as y

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ventotest")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
y.DATA_DIR = TMP
y.DB = y.Db(os.path.join(TMP, "ypod.db"))


def perto(a, b, tol=1.0):
    """Distancia angular, respeitando a volta do circulo."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d) <= tol


# --- 1. a conta em si ------------------------------------------------------
import math
sen = sum(math.sin(math.radians(g)) for g in (350, 10))
cos = sum(math.cos(math.radians(g)) for g in (350, 10))
ang = y.media_circular(sen / 2, cos / 2)
assert perto(ang, 0.0), "media de 350 e 10 deveria ser 0, deu %.1f" % ang
aritmetica = (350 + 10) / 2
assert perto(aritmetica, 180.0), "controle: a media aritmetica da 180"
print("[ok] 350° e 10° -> %.1f° (a media aritmetica daria %.0f°, o lado oposto)"
      % (ang, aritmetica))

# vetores opostos nao tem direcao media: tem que devolver None, nao um numero
assert y.media_circular(0.0, 0.0) is None
print("[ok] vetores que se cancelam devolvem 'sem direcao', nao um angulo falso")


# --- 2. o acumulador que grava no banco ------------------------------------
acum = y.AcumuladorPorMinuto(60)
base = 1800000000.0 - (1800000000.0 % 60)      # inicio de um minuto
leituras = [358.0, 359.0, 0.0, 1.0, 2.0, 355.0, 5.0]
for i, g in enumerate(leituras):
    acum.add(base + i, {"temp": 20.0, "vento_dir": g}, {}, "")
fechados = acum.fechar_tudo()
assert fechados, "o minuto deveria ter fechado"
_, medias, _, _, _, _ = fechados[0]
assert perto(medias["vento_dir"], 0.0, tol=2.0), \
    "direcao media deu %.1f, esperado perto de 0" % medias["vento_dir"]
assert abs(medias["temp"] - 20.0) < 0.01, "as nao-circulares seguem na media comum"
print("[ok] acumulador: %s -> %.1f° (e temp continua media aritmetica)"
      % (leituras, medias["vento_dir"]))


# --- 3. resumo por minuto e estatisticas -----------------------------------
# grava dois minutos de vento de norte, um valor por minuto
for minuto in range(2):
    for i, g in enumerate((357.0, 3.0)):
        ts = base + minuto * 60 + i * 10
        y.DB.insert(ts, {"temp": 20.0, "vento_dir": g}, {}, "",
                    src_key="t:%d" % (minuto * 100 + i))

st = y.DB.stats(base, base + 120)
d = st["vento_dir"]
assert d is not None, "deveria haver estatistica de direcao"
assert perto(d["avg"], 0.0, tol=2.0), "media da janela deu %.1f" % d["avg"]
assert d["min"] is None and d["max"] is None, \
    "minimo/maximo de angulo nao tem significado e devem vir vazios"
print("[ok] estatisticas da janela: media %.1f°, sem min/max (correto num circulo)"
      % d["avg"])

serie, balde = y.DB.series(base, base + 120)
pontos = serie["vento_dir"]
assert pontos, "a serie do grafico deveria ter pontos"
for _, g in pontos:
    assert perto(g, 0.0, tol=2.0), "ponto do grafico deu %.1f" % g
print("[ok] serie do grafico: %d ponto(s), todos perto de 0° (balde de %ds)"
      % (len(pontos), balde))

print("\nTUDO PASSOU")
