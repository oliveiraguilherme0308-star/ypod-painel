/*
 * Fonte USB host - le o pod pela porta USB, igual o PC faz
 * --------------------------------------------------------
 * Usa a biblioteca USBHost que vem no core do ESP32 a partir da versao 3.3.0.
 * Ela cuida de enumerar o dispositivo, achar a interface CDC-ACM, levantar o
 * DTR e entregar os bytes -- tudo o que antes estava escrito a mao aqui.
 *
 * Historico que vale guardar: a primeira versao deste arquivo falava direto com
 * a API USB do ESP-IDF, porque a documentacao que achamos dizia que o core
 * Arduino nao suportava USB host. Isso estava desatualizado. Com o driver
 * manual o pod era detectado no barramento (registrador HPRT acusava
 * dispositivo full-speed) mas nunca era enumerado. Trocar pela biblioteca
 * oficial resolveu sem mexer em uma linha de hardware.
 *
 * ---------------------------------------------------------------------------
 * LIGACAO na YD-ESP32-S3 (a placa com dois USB-C, marcados "COM" e "USB")
 * ---------------------------------------------------------------------------
 * - O cabo do pod vai no conector "USB" (o OTG nativo, GPIO19/20).
 * - O jumper de solda USB-OTG precisa estar FECHADO: e ele que leva os 5 V do
 *   conector "COM" ate o "USB", e sem esses 5 V o pod nem acende.
 * - Alimente e grave a placa pelo conector "COM".
 * - Deixe o pod na fonte externa dele. Alimentar os dois por uma porta USB so
 *   ja derrubou a alimentacao uma vez, por protecao do PC.
 * - NUNCA ligue "COM" e "USB" em computadores ao mesmo tempo: com o jumper
 *   fechado, um realimenta o outro.
 *
 * Em Ferramentas:  USB CDC On Boot = Disabled
 *                  USB Mode        = Hardware CDC and JTAG
 * (ligado, o core usa o OTG como dispositivo e briga com o modo host)
 */
#include "ponte.h"

#if FONTE_DO_POD == FONTE_USB_HOST

#include <USBHost.h>
#include <USBHostSerial.h>

USBHostSerialClass USBHostSerial;

static MontadorDeLinha s_montador;
static bool     s_montado = false;
static uint32_t s_bytes = 0;
static char     s_estado[72] = "iniciando";

void fonte_setup() {
  if (!USBHost.begin()) {
    Serial.println("[usb] USBHost.begin() falhou");
    snprintf(s_estado, sizeof(s_estado), "USB host nao iniciou");
    return;
  }
  // O baud e aplicado quando a interface CDC monta. Numa placa de USB nativa
  // (como o UNO R4) ele e virtual e ignorado, mas conversores de verdade
  // precisam dele -- e e aqui que o DTR sobe, que e o "tem alguem escutando".
  USBHostSerial.begin(YPOD_BAUD);
  snprintf(s_estado, sizeof(s_estado), "esperando o pod");
  Serial.println("[usb] host no ar - plugue o pod na porta USB (a OTG)");
}

void fonte_loop() {
  USBHost.task();

  if (!USBHostSerial) {
    if (s_montado) {
      s_montado = false;
      s_montador.limpar();
      snprintf(s_estado, sizeof(s_estado), "esperando o pod");
      Serial.println("[usb] pod desconectado");
    }
    return;
  }

  if (!s_montado) {
    s_montado = true;
    // Enumerar nao basta: num dispositivo CDC os dados so comecam a fluir
    // depois que o host levanta o DTR. O begin() faz isso de forma implicita
    // no mount, mas ja vimos a porta montar e ficar muda -- entao pedimos
    // explicitamente, com o baud do pod, antes de dar a leitura por boa.
    bool lc = USBHostSerial.setLineCoding(YPOD_BAUD, 0 /*1 stop*/,
                                          0 /*sem paridade*/, 8 /*bits*/);
    delay(20);
    bool cls = USBHostSerial.setControlLineState(0x0003);  // DTR | RTS
    snprintf(s_estado, sizeof(s_estado), "lendo o pod pela USB");
    Serial.printf("[usb] pod montado | line coding %s | DTR %s | baud=%lu\n",
                  lc ? "ok" : "recusado", cls ? "ok" : "recusado",
                  (unsigned long)USBHostSerial.baudRate());
  }

  // Diagnostico: se montou e nada chega, avisa de tempos em tempos com o que
  // a biblioteca diz ter em buffer.
  static uint32_t t_mudo = 0;
  if (s_bytes == 0) {
    if (!t_mudo) {
      t_mudo = millis();
    } else if (millis() - t_mudo > 8000) {
      t_mudo = millis();
      Serial.printf("[usb] montado e mudo ha 8s (available=%d)\n",
                    USBHostSerial.available());
    }
  }

  uint8_t buf[64];
  size_t n = USBHostSerial.read(buf, sizeof(buf));
  if (n > 0) {
    s_bytes += n;
    s_montador.bytes_recebidos(buf, n);
  }
}

void fonte_status(char *out, size_t n) {
  snprintf(out, n, "USB host - %s (%lu bytes)", s_estado, (unsigned long)s_bytes);
}

#endif  // FONTE_DO_POD == FONTE_USB_HOST
