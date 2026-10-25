#!/usr/bin/env python3
"""
Broker MQTT local, para testar o caminho MQTT sem depender do servidor da escola.

    python tools/broker_local.py

Sobe um broker na porta 1883 aceitando qualquer cliente, e mostra o IP que a
ponte deve usar. Quando o broker da escola existir, e so trocar o endereco em
ponte.h e no --broker do servidor: nada mais muda.

Nao e um broker de producao (e Python puro). Serve para provar o caminho.
"""
import asyncio
import socket
import sys

try:
    from amqtt.broker import Broker
except ImportError:
    print("falta o amqtt:  pip install amqtt")
    sys.exit(1)

PORTA = 1883


def meu_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


CONFIG = {
    "listeners": {
        "default": {"type": "tcp", "bind": "0.0.0.0:%d" % PORTA,
                    "max_connections": 50},
    },
    "sys_interval": 0,
    "auth": {"allow-anonymous": True},
    "topic-check": {"enabled": False},
}


async def main():
    broker = Broker(CONFIG)
    await broker.start()
    ip = meu_ip()
    print("broker MQTT no ar")
    print("  para a ponte (ponte.h):  mqtt://%s:%d" % (ip, PORTA))
    print("  para o servidor:         --broker mqtt://127.0.0.1:%d" % PORTA)
    print()
    print("Se a ponte nao conseguir conectar, quase sempre e o firewall do")
    print("Windows bloqueando a porta %d de entrada." % PORTA)
    print("Ctrl+C para parar.")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nbroker desligado.")
