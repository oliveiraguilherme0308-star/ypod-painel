/*
  YPOD_USB_Leitor.ino
  -------------------
  A ESP32-S3 funciona como "computador" (USB host) para o Arduino UNO R4 Minima
  do YPOD: abre a porta serial USB dele (classe CDC-ACM) a 9600 baud, liga o DTR
  (igual o PC faz quando abre o Monitor Serial) e recebe as linhas CSV.

  Cada linha recebida é mostrada no Monitor Serial da S3 (porta COM, 115200).
  O lugar para enviar os dados (Wi-Fi, ThingSpeak, servidor) é a função
  aoReceberLinha(), lá embaixo.

  LIGAÇÃO
    - YPOD (USB-C)  -> porta "USB" da S3 (a USB nativa / OTG)
    - Computador     -> porta "COM" da S3 (para gravar e ver o Monitor Serial)
    - Jumper IN-OUT da S3 fechado com solda (para a S3 mandar 5 V ao YPOD),
      ou o YPOD alimentado por fonte própria.

  CONFIGURAÇÃO NA ARDUINO IDE (menu Ferramentas)
    Placa ............... ESP32S3 Dev Module   (núcleo "esp32" da Espressif 3.x)
    USB CDC On Boot ..... Disabled
    USB Mode ............ Hardware CDC and JTAG
    Flash Size .......... 16MB (128Mb)
    PSRAM ............... OPI PSRAM
    Porta ............... a COM da porta "COM" da S3
*/

#include <Arduino.h>
#include "usb/usb_host.h"
#include "esp_intr_alloc.h"
#include "freertos/stream_buffer.h"

// ---------------- Ajustes ----------------
#define BAUD_YPOD       9600     // mesmo valor que funcionou no Monitor Serial do PC
#define TAMANHO_LINHA   512      // maior linha aceita (a do YPOD tem ~110 caracteres)
// -----------------------------------------

// Pedidos padrão da classe CDC-ACM (serial USB)
#define CDC_SET_LINE_CODING         0x20
#define CDC_SET_CONTROL_LINE_STATE  0x22

static usb_host_client_handle_t cliente = NULL;
static usb_device_handle_t      dispositivo = NULL;
static usb_transfer_t          *transfIn = NULL;     // recebe os dados do YPOD
static usb_transfer_t          *transfCtrl = NULL;   // comandos de configuração
static SemaphoreHandle_t        hostPronto = NULL;
static StreamBufferHandle_t     fluxo = NULL;        // ponte entre a tarefa USB e o loop()

static volatile uint8_t  enderecoNovo = 0;
static volatile bool     dispositivoSaiu = false;
static volatile bool     conectado = false;
static volatile bool     recebendo = false;          // há uma leitura USB em andamento
static volatile bool     ctrlTerminou = false;
static volatile usb_transfer_status_t ctrlStatus;

static uint8_t  epEntrada = 0;
static uint16_t tamPacote = 64;
static int      ifControle = -1;
static int      ifDados = -1;

// ======================= Callbacks da biblioteca USB =======================

static void eventoCliente(const usb_host_client_event_msg_t *msg, void *arg) {
  if (msg->event == USB_HOST_CLIENT_EVENT_NEW_DEV) {
    enderecoNovo = msg->new_dev.address;
  } else if (msg->event == USB_HOST_CLIENT_EVENT_DEV_GONE) {
    dispositivoSaiu = true;
  }
}

static void fimEntrada(usb_transfer_t *t) {
  if (t->status == USB_TRANSFER_STATUS_COMPLETED) {
    if (t->actual_num_bytes > 0) {
      xStreamBufferSend(fluxo, t->data_buffer, t->actual_num_bytes, 0);
    }
    if (conectado && usb_host_transfer_submit(t) == ESP_OK) return;  // pede o próximo pedaço
  }
  recebendo = false;
}

static void fimControle(usb_transfer_t *t) {
  ctrlStatus = t->status;
  ctrlTerminou = true;
}

// ======================= Funções auxiliares =======================

// Envia um comando de controle e espera a resposta (até 2 s)
static bool enviarControle(uint8_t bmRequestType, uint8_t bRequest, uint16_t wValue,
                           uint16_t wIndex, const uint8_t *dados, uint16_t tamanho) {
  usb_setup_packet_t *s = (usb_setup_packet_t *)transfCtrl->data_buffer;
  s->bmRequestType = bmRequestType;
  s->bRequest = bRequest;
  s->wValue = wValue;
  s->wIndex = wIndex;
  s->wLength = tamanho;
  if (tamanho) memcpy(transfCtrl->data_buffer + sizeof(usb_setup_packet_t), dados, tamanho);

  transfCtrl->num_bytes = sizeof(usb_setup_packet_t) + tamanho;
  transfCtrl->device_handle = dispositivo;
  transfCtrl->bEndpointAddress = 0;
  transfCtrl->callback = fimControle;
  transfCtrl->context = NULL;
  transfCtrl->timeout_ms = 1000;

  ctrlTerminou = false;
  if (usb_host_transfer_submit_control(cliente, transfCtrl) != ESP_OK) return false;

  uint32_t inicio = millis();
  while (!ctrlTerminou && millis() - inicio < 2000) {
    usb_host_client_handle_events(cliente, pdMS_TO_TICKS(10));
  }
  return ctrlTerminou && ctrlStatus == USB_TRANSFER_STATUS_COMPLETED;
}

// Procura no descritor a interface de dados CDC (classe 0x0A) e o endpoint bulk de entrada
static bool procurarInterfaces(const usb_config_desc_t *cfg) {
  const uint8_t *p = (const uint8_t *)cfg;
  uint16_t total = cfg->wTotalLength;
  int ifAtual = -1, classeAtual = -1;
  ifControle = ifDados = -1;
  epEntrada = 0;

  for (uint16_t i = 0; i + 1 < total && p[i] >= 2; i += p[i]) {
    uint8_t tipo = p[i + 1];
    if (tipo == 0x04 && i + 5 < total) {                 // descritor de interface
      ifAtual = p[i + 2];
      classeAtual = p[i + 5];
      if (classeAtual == 0x02 && ifControle < 0) ifControle = ifAtual;  // CDC controle
    } else if (tipo == 0x05 && i + 5 < total) {          // descritor de endpoint
      uint8_t endereco = p[i + 2];
      uint8_t atributos = p[i + 3];
      uint16_t mps = p[i + 4] | (p[i + 5] << 8);
      bool bulkEntrada = ((atributos & 0x03) == 0x02) && (endereco & 0x80);
      if (classeAtual == 0x0A && bulkEntrada && epEntrada == 0) {      // CDC dados
        ifDados = ifAtual;
        epEntrada = endereco;
        tamPacote = mps & 0x7FF;
      }
    }
  }
  return ifDados >= 0 && epEntrada != 0;
}

static void abrirDispositivo(uint8_t endereco) {
  if (usb_host_device_open(cliente, endereco, &dispositivo) != ESP_OK) {
    Serial.println("[USB] Não consegui abrir o dispositivo.");
    dispositivo = NULL;
    return;
  }

  const usb_device_desc_t *desc;
  usb_host_get_device_descriptor(dispositivo, &desc);
  Serial.printf("[USB] Dispositivo conectado: VID=%04X PID=%04X\n", desc->idVendor, desc->idProduct);

  const usb_config_desc_t *cfg;
  usb_host_get_active_config_descriptor(dispositivo, &cfg);
  if (!procurarInterfaces(cfg)) {
    Serial.println("[USB] Esse dispositivo não é uma serial USB padrão (CDC-ACM).");
    usb_host_device_close(cliente, dispositivo);
    dispositivo = NULL;
    return;
  }

  if (ifControle >= 0) usb_host_interface_claim(cliente, dispositivo, ifControle, 0);
  if (usb_host_interface_claim(cliente, dispositivo, ifDados, 0) != ESP_OK) {
    Serial.println("[USB] Não consegui assumir a interface de dados.");
    if (ifControle >= 0) usb_host_interface_release(cliente, dispositivo, ifControle);
    usb_host_device_close(cliente, dispositivo);
    dispositivo = NULL;
    return;
  }

  // Configura 9600 8N1. Nunca use 1200 baud: no Arduino isso o coloca em modo de gravação!
  uint16_t indice = (ifControle >= 0) ? ifControle : ifDados;
  uint8_t linha[7] = {
    (uint8_t)(BAUD_YPOD), (uint8_t)(BAUD_YPOD >> 8), (uint8_t)(BAUD_YPOD >> 16), (uint8_t)(BAUD_YPOD >> 24),
    0,   // 1 stop bit
    0,   // sem paridade
    8    // 8 bits
  };
  if (!enviarControle(0x21, CDC_SET_LINE_CODING, 0, indice, linha, sizeof(linha))) {
    Serial.println("[USB] Aviso: o dispositivo recusou a configuração de baud (seguindo mesmo assim).");
  }
  // Liga DTR e RTS: é o "tem alguém escutando" que o PC manda ao abrir a porta
  if (!enviarControle(0x21, CDC_SET_CONTROL_LINE_STATE, 0x0003, indice, NULL, 0)) {
    Serial.println("[USB] Aviso: o dispositivo recusou o sinal DTR (seguindo mesmo assim).");
  }

  usb_host_transfer_alloc(tamPacote, 0, &transfIn);
  transfIn->device_handle = dispositivo;
  transfIn->bEndpointAddress = epEntrada;
  transfIn->callback = fimEntrada;
  transfIn->context = NULL;
  transfIn->num_bytes = tamPacote;       // leitura precisa ser múltiplo do tamanho do pacote
  transfIn->timeout_ms = 0;

  conectado = true;
  recebendo = (usb_host_transfer_submit(transfIn) == ESP_OK);
  Serial.println("[USB] Lendo dados do YPOD...");
}

static void fecharDispositivo() {
  conectado = false;
  if (dispositivo == NULL) return;

  if (recebendo) {                       // cancela a leitura pendente
    usb_host_endpoint_halt(dispositivo, epEntrada);
    usb_host_endpoint_flush(dispositivo, epEntrada);
  }
  uint32_t inicio = millis();
  while (recebendo && millis() - inicio < 1000) {
    usb_host_client_handle_events(cliente, pdMS_TO_TICKS(10));
  }
  if (transfIn) { usb_host_transfer_free(transfIn); transfIn = NULL; }
  usb_host_interface_release(cliente, dispositivo, ifDados);
  if (ifControle >= 0) usb_host_interface_release(cliente, dispositivo, ifControle);
  usb_host_device_close(cliente, dispositivo);
  dispositivo = NULL;
  Serial.println("[USB] YPOD desconectado. Esperando ele voltar...");
}

// ======================= Tarefas do USB =======================

static void tarefaBibliotecaUsb(void *arg) {
  usb_host_config_t config = {};
  config.skip_phy_setup = false;
  config.intr_flags = ESP_INTR_FLAG_LEVEL1;
  ESP_ERROR_CHECK(usb_host_install(&config));
  xSemaphoreGive(hostPronto);

  while (true) {
    uint32_t flags;
    usb_host_lib_handle_events(portMAX_DELAY, &flags);
    if (flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) usb_host_device_free_all();
  }
}

static void tarefaCliente(void *arg) {
  xSemaphoreTake(hostPronto, portMAX_DELAY);

  usb_host_client_config_t config = {};
  config.is_synchronous = false;
  config.max_num_event_msg = 5;
  config.async.client_event_callback = eventoCliente;
  config.async.callback_arg = NULL;
  ESP_ERROR_CHECK(usb_host_client_register(&config, &cliente));
  usb_host_transfer_alloc(64, 0, &transfCtrl);

  while (true) {
    usb_host_client_handle_events(cliente, pdMS_TO_TICKS(50));

    if (enderecoNovo && dispositivo == NULL) {
      uint8_t e = enderecoNovo;
      enderecoNovo = 0;
      vTaskDelay(pdMS_TO_TICKS(100));
      abrirDispositivo(e);
    }
    if (dispositivoSaiu) {
      dispositivoSaiu = false;
      fecharDispositivo();
    }
    // Se a leitura parou por algum erro com o YPOD ainda ligado, tenta de novo
    if (conectado && !recebendo) {
      vTaskDelay(pdMS_TO_TICKS(200));
      usb_host_endpoint_clear(dispositivo, epEntrada);
      recebendo = (usb_host_transfer_submit(transfIn) == ESP_OK);
    }
  }
}

// ======================= LED de status (opcional) =======================
// Azul = esperando o YPOD | pisca verde = chegou uma linha.
// Em algumas placas clone o LED RGB fica no GPIO38 em vez do 48.
static void led(uint8_t r, uint8_t g, uint8_t b) {
#ifdef RGB_BUILTIN
  rgbLedWrite(RGB_BUILTIN, r, g, b);
#endif
}

// ======================= O que fazer com cada linha =======================

void aoReceberLinha(const String &linha) {
  Serial.print("[YPOD] ");
  Serial.println(linha);

  // Separa as colunas do CSV (só para conferir; os campos vazios também contam)
  int colunas = 1;
  for (size_t i = 0; i < linha.length(); i++) if (linha[i] == ',') colunas++;
  Serial.printf("       (%d colunas)\n", colunas);

  // >>> Aqui depois entra o envio por Wi-Fi para o ThingSpeak e o servidor da escola <<<
}

// ======================= setup / loop =======================

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("=== Leitor USB do YPOD (ESP32-S3) ===");
  led(0, 0, 20);

  fluxo = xStreamBufferCreate(4096, 1);
  hostPronto = xSemaphoreCreateBinary();
  xTaskCreatePinnedToCore(tarefaBibliotecaUsb, "usb_lib", 4096, NULL, 5, NULL, 0);
  xTaskCreatePinnedToCore(tarefaCliente, "usb_cli", 6144, NULL, 4, NULL, 0);

  Serial.println("Esperando o YPOD na porta USB...");
}

void loop() {
  static char linha[TAMANHO_LINHA];
  static size_t n = 0;
  static uint32_t ledAceso = 0;
  static bool estavaConectado = false;

  uint8_t buf[128];
  size_t lidos = xStreamBufferReceive(fluxo, buf, sizeof(buf), pdMS_TO_TICKS(20));
  for (size_t i = 0; i < lidos; i++) {
    char c = (char)buf[i];
    if (c == '\r') continue;
    if (c == '\n') {
      linha[n] = '\0';
      if (n > 0) {
        aoReceberLinha(String(linha));
        led(0, 30, 0);
        ledAceso = millis();
      }
      n = 0;
    } else if (n < TAMANHO_LINHA - 1) {
      linha[n++] = c;
    }
  }

  if (ledAceso && millis() - ledAceso > 150) {
    ledAceso = 0;
    led(0, 0, conectado ? 0 : 20);
  }
  if (conectado != estavaConectado) {
    estavaConectado = conectado;
    led(0, 0, conectado ? 0 : 20);
  }
}
