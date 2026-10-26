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

print("\nTUDO PASSOU")
