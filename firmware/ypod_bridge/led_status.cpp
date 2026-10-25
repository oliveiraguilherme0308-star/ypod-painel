/*
 * LED de status
 * -------------
 * Instalada em algum canto, sem cabo e sem rede, a ponte precisa dizer como
 * esta. Sao duas perguntas independentes -- "esta lendo o pod?" e "esta na
 * rede?" -- e o LED responde as duas sem misturar:
 *
 *   COR DE FUNDO = o pod (o que mais importa)
 *     verde pulsando a cada leitura .... lendo o pod
 *     amarelo .......................... o pod nao aparece ou emudeceu
 *
 *   PISCADA RAPIDA POR CIMA = a rede
 *     nenhuma .......................... na rede, tudo certo
 *     azul, de 2 em 2 s ................ procurando rede
 *     roxo, de 2 em 2 s ................ portal de configuracao aberto
 *
 * Assim um verde pulsando com piscada roxa quer dizer: esta coletando
 * normalmente e esperando voce configurar a rede. A versao anterior pintava
 * tudo de roxo e escondia justamente a informacao mais util.
 */
#include "ponte.h"

#if LED_LIGADO && defined(RGB_BUILTIN)

#include <WiFi.h>

static uint32_t s_ultimo_pulso = 0;
static uint32_t s_ultima_linha_vista = 0;

static void cor(uint8_t r, uint8_t g, uint8_t b) {
  rgbLedWrite(RGB_BUILTIN, r, g, b);
}

void led_setup() {
  cor(0, 0, LED_BRILHO);
}

void led_pulso() { s_ultimo_pulso = millis(); }

void led_loop() {
  const uint8_t B = LED_BRILHO;
  uint32_t agora = millis();

  // --- a piscada da rede tem prioridade, mas dura pouco ---
  bool sem_rede = WiFi.status() != WL_CONNECTED;
  if (sem_rede) {
    uint32_t fase = agora % 2000;
    if (fase < 140) {                    // 140 ms a cada 2 s
      if (rede_em_portal()) cor(B, 0, B);        // roxo: portal aberto
      else                  cor(0, 0, B);        // azul: procurando
      return;
    }
  }

  // --- o resto do tempo, a cor conta sobre o pod ---
  bool pod_vivo = lastLineMs && (agora - lastLineMs < 30000);
  if (!pod_vivo) {
    cor(B, B, 0);                        // amarelo: sem dados do pod
    return;
  }
  if (seqCount != s_ultima_linha_vista) {
    s_ultima_linha_vista = seqCount;
    led_pulso();
  }
  bool pulsando = agora - s_ultimo_pulso < 120;
  cor(0, pulsando ? B * 3 : B / 3, 0);   // verde, pulso a cada leitura
}

#else   // placa sem LED RGB, ou LED desligado em ponte.h

void led_setup() {}
void led_pulso() {}
void led_loop() {}

#endif
