"""
Roda a suite inteira e devolve codigo de saida 0 so se tudo passar.

    python testes/todos.py            # os rapidos (~1 min)
    python testes/todos.py --tudo     # inclui o de volume (5,2 M leituras)
    python testes/todos.py --vezes 5  # repete, para achar teste instavel

Existe porque sem ele cada um roda os testes que lembra. O volume_test ficou
dois dias quebrado sem ninguem perceber, e o nuvem_test falhava uma vez a cada
tres -- os dois teriam aparecido no primeiro dia.

O --vezes e para teste INSTAVEL, que e pior que teste quebrado: quebrado todo
mundo ve, instavel vira "deve ser a rede" e some do radar.
"""
import os
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))

# Ordem deliberada: os que nao abrem socket vem primeiro, para um erro de
# importacao aparecer em 2 s em vez de depois de um minuto de rede.
RAPIDOS = [
    "parser_test.py",      # formato da linha, lixo do pod
    "iqar_test.py",        # indice de qualidade do ar (CONAMA 506/2024)
    "vento_test.py",       # media circular da direcao
    "gravacao_test.py",    # media por minuto
    "contrato_test.py",    # firmware <-> servidor
    "net_test.py",         # TCP, backlog, dedupe
    "mqtt_test.py",        # MQTT QoS 1 (precisa de broker local)
    "influx_test.py",      # fila duravel do InfluxDB
    "nuvem_test.py",       # envio para o Worker
]
LENTOS = ["volume_test.py"]          # ~3 min e ~600 MB em disco


def rodar(nome):
    t = time.time()
    r = subprocess.run([sys.executable, "-I", os.path.join(AQUI, nome)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode == 0, time.time() - t, (r.stdout or "") + (r.stderr or "")


def main():
    tudo = "--tudo" in sys.argv
    vezes = 1
    if "--vezes" in sys.argv:
        vezes = int(sys.argv[sys.argv.index("--vezes") + 1])

    lista = RAPIDOS + (LENTOS if tudo else [])
    falhas = {}
    t0 = time.time()

    for volta in range(vezes):
        if vezes > 1:
            print("\n=== volta %d de %d ===" % (volta + 1, vezes))
        for nome in lista:
            ok, seg, saida = rodar(nome)
            print("  %-18s %s  %5.1fs" % (nome, "ok  " if ok else "FALHOU", seg))
            if not ok:
                falhas.setdefault(nome, []).append(saida)

    print("\n%s" % ("-" * 52))
    total = len(lista) * vezes
    if not falhas:
        print("%d execucoes, todas passaram, em %.0fs" % (total, time.time() - t0))
        if not tudo:
            print("(o volume_test ficou de fora; use --tudo para inclui-lo)")
        return 0

    print("%d de %d execucoes falharam:\n" % (sum(len(v) for v in falhas.values()), total))
    for nome, saidas in falhas.items():
        instavel = vezes > 1 and len(saidas) < vezes
        print("  %s%s" % (nome, "   <- INSTAVEL (nem sempre falha)" if instavel else ""))
        ultimas = saidas[-1].strip().splitlines()[-6:]
        for linha in ultimas:
            print("      %s" % linha)
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
