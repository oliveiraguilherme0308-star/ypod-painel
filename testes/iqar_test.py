"""
IQAr - Indice de Qualidade do Ar (CONAMA 506/2024).

Os limites da faixa "Boa" sao os do Anexo II da resolucao, lidos da integra.
As fronteiras Moderada->Ruim sao os PI-2 do Anexo I. Este teste trava esses
valores: se alguem "atualizar" a tabela com a versao antiga que ainda circula
(MP10 0-50, MP2,5 0-25, da CONAMA 491/2018), quebra aqui.
"""
import os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ypod_monitor as y

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iqartest")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
y.DATA_DIR = TMP
y.DB = y.Db(os.path.join(TMP, "ypod.db"))

# --- 1. os limites da norma -----------------------------------------------
# Anexo II: a faixa "Boa" vai ate indice 40, e termina em 45 (MP10) e 15 (MP2,5)
assert y.iqar_sub(45.0, "pm10") == 40.0, y.iqar_sub(45.0, "pm10")
assert y.iqar_sub(15.0, "pm25") == 40.0, y.iqar_sub(15.0, "pm25")
assert y.iqar_sub(9.0, "co") == 40.0
print("[ok] fim da faixa Boa = indice 40: MP10 45, MP2,5 15, CO 9 ppm")

# A tabela ANTIGA (CONAMA 491/2018) terminava a faixa Boa em 50 e 25. Se
# alguem reintroduzir, 50 daria exatamente 40 em vez de ~43.6.
assert y.iqar_sub(50.0, "pm10") != 40.0, "tabela antiga da 491/2018 voltou"
print("[ok] a tabela antiga (MP10 ate 50) nao esta em uso")

# PI-2 do Anexo I: fronteira Moderada -> Ruim
assert y.iqar_sub(100.0, "pm10") == 80.0
assert y.iqar_sub(50.0, "pm25") == 80.0
print("[ok] fronteira Moderada/Ruim nos PI-2: MP10 100, MP2,5 50")

# As outras tres fronteiras, conferidas contra a Tabela 5 do relatorio anual
# do IEMA-ES 2024, que reproduz as faixas do MMA (jan/2025).
for conc, pol, esperado, onde in (
        (150.0, "pm10", 120.0, "IT-1 da OMS 2021"),
        (75.0,  "pm25", 120.0, "IT-1 da OMS 2021"),
        (250.0, "pm10", 200.0, "nivel de atencao da 491/18"),
        (125.0, "pm25", 200.0, "nivel de atencao da 491/18"),
        (600.0, "pm10", 400.0, "topo da faixa Pessima"),
        (300.0, "pm25", 400.0, "topo da faixa Pessima")):
    got = y.iqar_sub(conc, pol)
    assert got == esperado, "%s %.0f -> %s, esperado %s (%s)" % (
        pol, conc, got, esperado, onde)
print("[ok] Ruim/MuitoRuim, MuitoRuim/Pessima e o topo batem com a Tabela 5")

# CO: a norma usa ppm; o relatorio do IEMA converteu para µg/m³. A 25 °C,
# 1 ppm = 1.145,7 µg/m³ -- conferindo os quatro limites da tabela deles.
for ppm, ug in ((9, 10314), (11, 12606), (13, 14898), (15, 17190)):
    calc = ppm * 1145.7
    assert abs(calc - ug) / ug < 0.002, "%d ppm -> %.0f, tabela diz %d" % (ppm, calc, ug)
print("[ok] limites de CO em ppm equivalem aos µg/m³ da Tabela 5 (9, 11, 13, 15)")

# --- 2. a interpolacao ----------------------------------------------------
# Meio da primeira faixa de MP2,5: 7,5 µg/m³ -> metade de 40
assert abs(y.iqar_sub(7.5, "pm25") - 20.0) < 0.01, y.iqar_sub(7.5, "pm25")
print("[ok] interpolacao linear: MP2,5 de 7,5 µg/m³ -> indice 20")

# --- 3. o indice e o MAIOR dos sub-indices --------------------------------
r = y.iqar({"pm25": 7.5, "pm10": 120.0})   # 20 e ~93
assert r["dominante"] == "pm10", r
assert r["indice"] > 80, r
assert r["classe"] == "Ruim", r["classe"]
print("[ok] vence o pior poluente: MP10 120 µg/m³ -> %d, %s"
      % (r["indice"], r["classe"]))

# --- 4. diz com o que foi calculado ---------------------------------------
r = y.iqar({"pm25": 10.0, "pm10": 30.0, "co": None})
assert r["calculado_com"] == ["pm10", "pm25"], r["calculado_com"]
assert "co" not in r["sub"]
print("[ok] declara de quais poluentes saiu: %s" % r["calculado_com"])

assert y.iqar({"co": None}) is None
assert y.iqar({}) is None
print("[ok] sem poluente nenhum devolve None, nao zero")

# --- 5. classificacao -----------------------------------------------------
for conc, esperado in ((10.0, "Boa"), (30.0, "Moderada"), (60.0, "Ruim"),
                       (100.0, "Muito ruim"), (200.0, "Péssima")):
    r = y.iqar({"pm25": conc})
    assert r["classe"] == esperado, "MP2,5 %.0f -> %s (esperado %s)" % (
        conc, r["classe"], esperado)
print("[ok] as cinco classes saem nas concentracoes certas de MP2,5")

# --- 6. janela movel de 24 h, do banco ------------------------------------
import time
agora = time.time()
# 24 h de MP2,5 em 30 µg/m³ (faixa Moderada) e MP10 baixo
for i in range(0, 24 * 60, 5):
    ts = agora - i * 60
    y.DB.insert(ts, {"pm25": 30.0, "pm10": 20.0}, {}, "", src_key="t:%d" % i)

r = y.DB.iqar_agora()
assert r is not None, "deveria calcular com 24 h de dados"
assert r["dominante"] == "pm25", r
assert r["classe"] == "Moderada", r["classe"]
assert r["janelas_h"]["pm25"] == 24, r["janelas_h"]
assert abs(r["medias"]["pm25"] - 30.0) < 0.5, r["medias"]
print("[ok] janela de 24 h do banco: media %.1f µg/m³ -> indice %d (%s), %d amostras"
      % (r["medias"]["pm25"], r["indice"], r["classe"], r["amostras"]["pm25"]))

# o valor do MINUTO nao manda: um pico recente nao deve estourar o indice
y.DB.insert(agora + 1, {"pm25": 400.0, "pm10": 20.0}, {}, "", src_key="pico")
r2 = y.DB.iqar_agora()
assert r2["classe"] == "Moderada", (
    "um pico de 1 minuto mudou o indice de 24 h: %s" % r2["classe"])
print("[ok] pico de 1 minuto nao desloca a media de 24 h (%s -> %s)"
      % (r["classe"], r2["classe"]))

print("\nTUDO PASSOU")
