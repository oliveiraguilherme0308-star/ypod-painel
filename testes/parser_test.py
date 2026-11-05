"""
Linhas malformadas que o parser aceitava.

As quatro primeiras sao LINHAS REAIS, copiadas do banco em 24/10/2026. Elas
passaram pelos filtros e gravaram 4665 graus e 2876% de umidade -- nao por
defeito de sensor, mas porque o parser le por POSICAO e a linha tinha chegado
deslocada. Um numero absurdo num grafico achata a escala de todo o resto.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ypod_monitor as y

BOA = ("2026-07-31T10:53:37,,,YPODV6,YPOD_V4.2.1,,,28.84,61.27,,2577,468,"
       "65535,,4496,2868,1390.00,16,28,28,")

# --- 1. a linha boa continua passando -------------------------------------
r = y.parse_line(BOA)
assert r is not None, "a linha boa foi rejeitada"
_, v, meta = r
assert abs(v["temp"] - 28.84) < 0.01, v["temp"]
assert abs(v["rh"] - 61.27) < 0.01, v["rh"]
assert v["o3"] is None, "65535 no canal do ozonio: entrada aberta, sai vazio"
assert meta["pod_id"] == "YPODV6", meta
print("[ok] linha normal: temp %.2f, rh %.2f, ozonio vazio, pod %s"
      % (v["temp"], v["rh"], meta["pod_id"]))

# com o vento que a ponte acrescenta no fim
r = y.parse_line(BOA + "123.4,47.5")
assert r is not None
_, v, _ = r
assert abs(v["vento_vel"] - 123.4) < 0.01, v["vento_vel"]
assert abs(v["vento_dir"] - 47.5) < 0.01, v["vento_dir"]
print("[ok] linha com vento: %.1f rpm, %.1f graus" % (v["vento_vel"], v["vento_dir"]))

# --- 2. linha truncada (REAL, 08/10 20:26) --------------------------------
# Chegou sem os 7 primeiros campos. O parser lia a casa 7 e encontrava 4665,
# que e a leitura do sensor de CO, e gravava como temperatura.
TRUNCADA = "28.65,61.09,,2564,415,65535,,4665,2876,1170.00,19,30,30,"
assert y.parse_line(TRUNCADA) is None, "linha truncada ainda e aceita"
print("[ok] linha truncada rejeitada (gravava 4665 graus na temperatura)")

# --- 3. virgula corrompida (REAL, 08/10 20:40) ----------------------------
# "YPODV6YYPOD_V4.2.1": a virgula virou Y. Dois campos viram um, tudo desloca
# uma casa, e a umidade foi gravada como temperatura.
CORROMPIDA = ("2026-07-31T11:11:45,,,YPODV6YYPOD_V4.2.1,,,27.69,62.11,,2546,"
              "438,65534,,4413,2888,940.00,20,36,44,")
assert len(CORROMPIDA.split(",")) == 20, "esta linha tem um campo a menos"
assert len(BOA.split(",")) == 21, "a linha boa tem 21"
assert y.parse_line(CORROMPIDA) is None, \
    "linha com um campo a menos ainda e aceita (gravava a umidade como temp)"
print("[ok] linha com virgula corrompida rejeitada (20 campos, deveria ter 21)")

# --- 4. pod em mau estado (REAL, 22/10) -----------------------------------
MAU_ESTADO = ("2000-84-90T00:03:00,,,YPODV6,YPOD_V4.2.1,,,128.86,118.99,,"
              "64537,64537,64537,,64537,64537,1.00,1,2,2,")
assert y.parse_line(MAU_ESTADO) is None, "data impossivel ainda e aceita"
print("[ok] data impossivel (2000-84-90) rejeita a linha inteira")

# --- 5. faixa fisica: o valor cai, a linha fica ---------------------------
# Uma linha bem formada com uma temperatura impossivel: perde so a temperatura.
FORA = ("2026-07-31T10:53:37,,,YPODV6,YPOD_V4.2.1,,,999.5,61.27,,2577,468,"
        "65535,,4496,2868,1390.00,16,28,28,")
r = y.parse_line(FORA)
assert r is not None, "a linha inteira nao deveria cair por um sensor fora"
_, v, _ = r
assert v["temp"] is None, "temp 999.5 deveria ter sido descartada"
assert abs(v["rh"] - 61.27) < 0.01, "os outros sensores continuam valendo"
assert v["pm25"] == 28, "os outros sensores continuam valendo"
print("[ok] temp fora da faixa descartada; rh e pm25 da mesma linha preservados")

# umidade acima de 100% nao existe
FORA_RH = BOA.replace(",28.84,61.27,", ",28.84,118.99,")
_, v, _ = y.parse_line(FORA_RH)
assert v["rh"] is None and abs(v["temp"] - 28.84) < 0.01
print("[ok] umidade de 118,99% descartada, temperatura da mesma linha mantida")

# direcao do vento fora do circulo
_, v, _ = y.parse_line(BOA + "100.0,400.0")
assert v["vento_dir"] is None and abs(v["vento_vel"] - 100.0) < 0.01
print("[ok] direcao de 400 graus descartada, velocidade mantida")

# --- 6. linha curta -------------------------------------------------------
assert y.parse_line("2026-07-31T10:53:37,,,YPODV6,YPOD_V4.2.1,,,28.84") is None
print("[ok] linha curta demais rejeitada")


# --- 7. o painel esconde de verdade o que marca como escondido -------------
# O atributo hidden do HTML e display:none pela folha do navegador, e qualquer
# regra de autor com display vence ela. O bloco do IQAr tem display:flex --
# sem uma regra explicita para [hidden], ele aparecia vazio no site publicado.
import re as _re
_painel = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "dashboard.html")
_html = open(_painel, encoding="utf-8").read()
_usa_hidden = _re.findall(r"\.hidden\s*=|<\w+[^>]*\shidden[\s>]", _html)
assert _usa_hidden, "ninguem usa hidden? conferir este teste"
assert _re.search(r"\[hidden\]\s*\{[^}]*display\s*:\s*none", _html), (
    "o painel usa hidden em %d lugares mas nao tem regra [hidden]{display:none}"
    % len(_usa_hidden))
print("[ok] painel: %d usos de hidden, com a regra CSS que os faz valer"
      % len(_usa_hidden))


# --- 8. valor calculado pelo pod nao sobrevive a entrada invalida ----------
# Desde 30/10 o pod roda CALIBRATE 1: tvoc, co e co2 chegam prontos da equacao
# de calibracao dele. Se uma entrada da equacao era lixo, a saida e lixo com
# cara de numero bom -- e nenhuma faixa fisica pega isso.
#
# Linha real do pod calibrado (30/10, ar limpo de sala):
CAL = ("2026-07-31T17:49:53,,,YPODV6,YPOD_V4.2.1,,,23.95,59.11,0,1538,166,"
       "65535,0.00,2851,2942,317,1,2,2,")
_, v, _ = y.parse_line(CAL)
assert v["tvoc"] == 0.0 and v["co"] == 0.0, (v["tvoc"], v["co"])
assert v["co2"] == 317.0, v["co2"]
print("[ok] linha calibrada: tvoc 0, co 0 ppm, co2 317 ppm (ar limpo)")

# Mesma linha com o ADS1115 nao inicializado: os dois Figaro leem 64537.
# SATURADO derruba os campos 10 e 11; o tvoc do campo 9 veio de
# 0.05183 * 64537 = 3345 e PASSARIA, porque 3345 nao e absurdo para COV.
ADS_MORTO = CAL.replace(",0,1538,166,", ",3345,64537,64537,")
_, v, _ = y.parse_line(ADS_MORTO)
assert v["fig2600"] is None and v["fig2602"] is None, "64537 deveria cair"
assert v["tvoc"] is None, \
    "tvoc %r passou, mas foi calculado de fig2600/fig2602 invalidos" % v["tvoc"]
assert v["temp"] == 23.95 and v["pm25"] == 2.0, "o resto da linha continua bom"
print("[ok] ADS1115 morto: tvoc descartado junto com os Figaro que o geraram")

# Sem o SHT25 a equacao do CO tambem perde o pe: ela usa rh.
SEM_RH = CAL.replace(",23.95,59.11,", ",23.95,118.99,")
_, v, _ = y.parse_line(SEM_RH)
assert v["rh"] is None, "118,99% deveria cair"
assert v["co"] is None, "co %r passou sem o rh que a equacao usa" % v["co"]
assert v["co2"] is None, "co2 %r passou sem o rh que a equacao usa" % v["co2"]
assert v["pm25"] == 2.0, "o que nao depende de rh continua valendo"
print("[ok] rh invalido: co e co2 descartados; pm25 da mesma linha mantido")

# E o contrario: linha SEM calibracao (campos 9 e 13 vazios) nao passa por
# essa regra -- ali o co2 e leitura crua e nao depende de rh nem de temp.
# E o caso de todo o historico anterior a 30/10.
SEM_CAL = BOA.replace(",28.84,61.27,", ",28.84,118.99,")
_, v, _ = y.parse_line(SEM_CAL)
assert v["rh"] is None, "118,99% deveria cair"
assert v["co2"] == 1390.0, \
    "co2 cru %r foi descartado por causa do rh, mas nao depende dele" % v["co2"]
print("[ok] linha sem CALIBRATE: co2 cru preservado apesar do rh invalido")

print("\nTUDO PASSOU")
