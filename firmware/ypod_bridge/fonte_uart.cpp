/*
 * Fonte UART - fio no pino TX do pod
 * ----------------------------------
 * Alternativa ao USB host. So vale a pena se o firmware do pod escrever em
 * Serial1: no Arduino UNO R4 a USB e nativa do chip e NAO passa por D0/D1,
 * entao com Serial.print() este caminho nao recebe nada.
 *
 * Ligacao (o pod e 5 V, o ESP32-S3 aceita no maximo 3,6 V na entrada):
 *
 *     YPOD D1 / TX (5V) ---[ 10k ]---+--- ESP32 GPIO18
 *                                    |
 *                                  [ 20k ]
 *                                    |
 *     YPOD GND ----------------------+--- ESP32 GND
 */
#include "ponte.h"

#if FONTE_DO_POD == FONTE_UART

static HardwareSerial  PodSerial(1);
static MontadorDeLinha s_montador;
static uint32_t        s_bytes = 0;

void fonte_setup() {
  PodSerial.begin(YPOD_BAUD, SERIAL_8N1, YPOD_RX_PIN, YPOD_TX_PIN);
  Serial.printf("[uart] lendo o pod no GPIO%d a %ld bps\n", YPOD_RX_PIN, YPOD_BAUD);
}

void fonte_loop() {
  while (PodSerial.available()) {
    s_montador.byte_recebido((uint8_t)PodSerial.read());
    s_bytes++;
  }
}

void fonte_status(char *out, size_t n) {
  snprintf(out, n, "UART no GPIO%d a %ld bps (%lu bytes)",
           YPOD_RX_PIN, YPOD_BAUD, (unsigned long)s_bytes);
}

#endif  // FONTE_DO_POD == FONTE_UART
