/*
 * Pagina de configuracao da ponte
 * -------------------------------
 * http://ypod-bridge.local/config
 *
 * Existe porque algumas coisas so se descobrem com a ponte montada no lugar
 * definitivo: onde fica o norte da biruta, quantos furos o disco realmente tem,
 * quanto vale um rpm em m/s. Recompilar e regravar a cada tentativa tornaria a
 * calibracao insuportavel -- e depois de instalada, a placa pode estar num
 * mastro, longe de qualquer cabo.
 *
 * Tudo que se ajusta aqui mora na NVS e sobrevive a reinicio e a regravacao de
 * firmware. Os valores de ponte.h viram apenas o ponto de partida de fabrica.
 *
 * A pagina de status (/) continua aberta e so de leitura: e ela que responde
 * "o problema e a rede ou e o pod?", e por em frente de uma senha atrapalharia
 * o diagnostico. Esta aqui, que MEXE nas coisas, pede senha.
 */
#include "ponte.h"
#include <WebServer.h>
#include <WiFi.h>

extern WebServer http;

// --------------------------------------------------------------------------
// Autenticacao
// --------------------------------------------------------------------------
// Reusa a senha do portal, que o usuario ja pode trocar aqui mesmo. Enquanto
// ela for a de fabrica, a protecao e simbolica -- e a pagina diz isso em voz
// alta, em vez de fingir que esta trancada.
static const char *USUARIO = "ypod";

static bool autorizado() {
  String senha = rede_senha_ap();
  if (senha.length() == 0) return true;            // sem senha configurada
  if (http.authenticate(USUARIO, senha.c_str())) return true;
  http.requestAuthentication();
  return false;
}

// --------------------------------------------------------------------------
static const char *ESTILO =
    "<!doctype html><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>YPOD Bridge - configuracao</title><style>"
    "body{font:15px system-ui;background:#1a1a19;color:#fff;margin:0;padding:24px;"
    "max-width:620px;margin:0 auto}"
    "h1{font-size:18px;margin:0 0 4px}h2{font-size:15px;margin:28px 0 8px;color:#c3c2b7}"
    "p{color:#8a8980;font-size:13.5px;margin:0 0 14px;line-height:1.5}"
    "label{display:block;font-size:13px;color:#c3c2b7;margin:12px 0 5px}"
    "input{width:100%;box-sizing:border-box;padding:10px;font-size:16px;"
    "background:#232320;color:#fff;border:1px solid #33332f;border-radius:8px}"
    "button{padding:11px 16px;font-size:15px;font-weight:600;background:#3987e5;"
    "color:#fff;border:0;border-radius:8px;cursor:pointer;margin-top:14px}"
    "button.alt{background:#33332f}button.perigo{background:#8c3b3b}"
    "table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}"
    "td{padding:5px 12px 5px 0;border-bottom:1px solid #33332f;font-size:14px}"
    "td:first-child{color:#8a8980;width:42%}"
    ".aviso{background:#3a2f1c;border:1px solid #6b5520;color:#e8d9a8;padding:11px 13px;"
    "border-radius:8px;font-size:13.5px;margin:14px 0}"
    ".ok{background:#1e3a2a;border:1px solid #2f6b47;color:#a8e8c4;padding:11px 13px;"
    "border-radius:8px;font-size:13.5px;margin:14px 0}"
    "a{color:#3bbd7e}form{margin:0}"
    "</style>";

static void responder(int codigo, const String &corpo) {
  http.send(codigo, "text/html; charset=utf-8", corpo);
}

// Volta para /config mostrando um recado. O redirecionamento existe para o
// F5 nao reenviar o formulario.
static void voltar(const char *msg) {
  http.sendHeader("Location", String("/config?m=") + msg);
  http.send(303, "text/plain", "ok");
}

// --------------------------------------------------------------------------
static void pagina() {
  if (!autorizado()) return;

  String m = http.arg("m");
  String h = ESTILO;
  h += "<h1>Configuração da ponte</h1>";
  h += "<p>O que você ajustar aqui fica gravado na placa e sobrevive a "
       "reinício e a nova gravação de firmware. "
       "<a href='/'>Ver a página de status</a></p>";

  if (m == "norte")   h += "<div class=ok>Norte definido na posição atual da biruta.</div>";
  if (m == "calib")   h += "<div class=ok>Calibração do tacômetro gravada.</div>";
  if (m == "senha")   h += "<div class=ok>Senha do portal trocada. Ela vale a partir de agora, inclusive para entrar nesta página.</div>";
  if (m == "padrao")  h += "<div class=ok>Senha do portal voltou ao padrão de fábrica.</div>";
  if (m == "curta")   h += "<div class=aviso>Senha não trocada: o Wi-Fi exige no mínimo 8 caracteres.</div>";
  if (m == "invalido")h += "<div class=aviso>Valores recusados: os furos têm que ser 1 ou mais, e o fator não pode ser negativo.</div>";

  if (rede_senha_ap_e_padrao()) {
    h += "<div class=aviso><b>A senha desta página é a de fábrica</b> "
         "(<code>" + rede_senha_ap() + "</code>), que está publicada junto com o "
         "código. Qualquer pessoa na mesma rede consegue entrar aqui. Troque "
         "logo abaixo se a ponte for ficar instalada em lugar compartilhado.</div>";
  }

  // ---- vento ----
  h += "<h2>Direção do vento</h2>";
  h += "<table>";
  h += "<tr><td>ímã detectado</td><td>" + String(vento_valido() ? "sim" : "<b>não</b> — aproxime o ímã (0,5 a 3 mm)") + "</td></tr>";
  h += "<tr><td>ângulo cru do sensor</td><td>" + String(vento_angulo_cru()) + " / 4095</td></tr>";
  h += "<tr><td>norte gravado</td><td>" + String(vento_offset()) + "</td></tr>";
  h += "<tr><td>direção agora</td><td>" + String(vento_direcao(), 1) + "°</td></tr>";
  h += "</table>";
  h += "<p>Aponte a biruta para o norte <b>antes</b> de clicar. O ângulo que "
       "estiver valendo neste instante passa a ser o zero.</p>";
  h += "<form method=POST action='/config/norte'>"
       "<button type=submit>Definir norte na posição atual</button></form>";

  // ---- tacometro ----
  h += "<h2>Velocidade do vento</h2>";
  h += "<table>";
  h += "<tr><td>rotação agora</td><td>" + String(vento_rpm(), 1) + " rpm</td></tr>";
  h += "<tr><td>publicando</td><td>" + String(vento_k() > 0 ? String(vento_velocidade(), 2) + " m/s" : String("rpm cru")) + "</td></tr>";
  h += "</table>";
  h += "<p>O fator converte rotação em m/s: <code>v = k &times; rpm</code>. "
       "Deixe <b>0</b> enquanto não houver comparação com um anemômetro de "
       "referência — um fator inventado faz o número parecer uma velocidade "
       "sem ser. Os furos têm que bater com o disco: errado ali, a velocidade "
       "sai proporcional mas com a escala toda errada.</p>";
  h += "<form method=POST action='/config/calibrar'>";
  h += "<label>Furos do disco</label>";
  h += "<input name=furos type=number min=1 step=1 value='" + String(vento_furos()) + "'>";
  h += "<label>Fator k (0 = publicar rpm cru)</label>";
  h += "<input name=k type=number min=0 step=0.0001 value='" + String(vento_k(), 4) + "'>";
  h += "<button type=submit>Gravar calibração</button></form>";

  // ---- senha ----
  h += "<h2>Senha do portal</h2>";
  h += "<p>Vale para a rede <code>" AP_SETUP_SSID "</code> que aparece quando a "
       "ponte fica sem Wi-Fi, e para entrar nesta página. Mínimo 8 caracteres. "
       "Em branco volta ao padrão de fábrica.</p>";
  h += "<form method=POST action='/config/senha'>";
  h += "<label>Nova senha</label>";
  h += "<input name=senha type=password placeholder='8 caracteres ou mais'>";
  h += "<button type=submit>Trocar</button></form>";

  // ---- rede ----
  h += "<h2>Rede</h2>";
  h += "<table>";
  h += "<tr><td>rede atual</td><td>" + (WiFi.status() == WL_CONNECTED ? WiFi.SSID() : String("sem rede")) + "</td></tr>";
  h += "<tr><td>IP</td><td>" + WiFi.localIP().toString() + "</td></tr>";
  h += "<tr><td>sinal</td><td>" + String(WiFi.RSSI()) + " dBm</td></tr>";
  h += "</table>";
  h += "<p>Abrir o portal derruba a ponte da rede por até 5 minutos para ela "
       "virar ponto de acesso. A coleta do pod continua rodando o tempo todo, e "
       "o que for lido fica no buffer.</p>";
  h += "<form method=POST action='/config/portal'>"
       "<button class=alt type=submit>Abrir o portal de rede agora</button></form>";
  h += "<form method=POST action='/config/reiniciar'>"
       "<button class=perigo type=submit>Reiniciar a ponte</button></form>";
  h += "<p style='margin-top:10px'>Reiniciar perde o buffer e exige replugar a "
       "USB do pod — ele não reaparece sozinho depois do boot.</p>";

  responder(200, h);
}

// --------------------------------------------------------------------------
void config_rotas() {
  http.on("/config", HTTP_GET, pagina);

  http.on("/config/norte", HTTP_POST, []() {
    if (!autorizado()) return;
    vento_definir_norte();
    voltar("norte");
  });

  http.on("/config/calibrar", HTTP_POST, []() {
    if (!autorizado()) return;
    int furos = http.arg("furos").toInt();
    float k = http.arg("k").toFloat();
    voltar(vento_definir_calibracao(furos, k) ? "calib" : "invalido");
  });

  http.on("/config/senha", HTTP_POST, []() {
    if (!autorizado()) return;
    String nova = http.arg("senha");
    if (!rede_definir_senha_ap(nova)) { voltar("curta"); return; }
    voltar(nova.length() ? "senha" : "padrao");
  });

  http.on("/config/portal", HTTP_POST, []() {
    if (!autorizado()) return;
    String h = ESTILO;
    h += "<h1>Abrindo o portal</h1><p>Procure a rede <code>" AP_SETUP_SSID
         "</code> e abra <code>http://192.168.4.1</code>. Se ninguem configurar "
         "nada, em 5 minutos a ponte volta sozinha para a rede salva.</p>";
    responder(200, h);
    http.client().flush();
    delay(300);
    rede_abrir_portal();
  });

  // Reiniciar e a unica acao daqui que destroi alguma coisa: o buffer inteiro
  // se perde (ate ~13 dias de dados que ainda nao chegaram ao PC) e o pod nao
  // volta sozinho, exige ir ate la replugar a USB. Um toque sem querer no
  // celular nao pode fazer isso, entao pede um segundo clique.
  //
  // A confirmacao e uma pagina, nao um confirm() de JavaScript: o resto desta
  // pagina nao usa script nenhum, e navegador de celular as vezes engole
  // dialogo de confirmacao sem avisar.
  http.on("/config/reiniciar", HTTP_POST, []() {
    if (!autorizado()) return;
    if (http.arg("confirmar") != "1") {
      String c = ESTILO;
      c += "<h1>Reiniciar a ponte?</h1>";
      c += "<div class=aviso><b>Isto apaga o buffer.</b> Tudo que a ponte "
           "guardou e o PC ainda não recebeu se perde — e depois do boot o pod "
           "não reaparece sozinho, alguém precisa replugar a USB dele.</div>";
      c += "<p>Se o objetivo é só trocar de rede, use <b>abrir o portal</b> na "
           "página anterior: o portal não reinicia a placa e a coleta continua.</p>";
      c += "<form method=POST action='/config/reiniciar'>"
           "<input type=hidden name=confirmar value='1'>"
           "<button class=perigo type=submit>Sim, reiniciar agora</button></form>";
      c += "<p style='margin-top:18px'><a href='/config'>Voltar sem reiniciar</a></p>";
      responder(200, c);
      return;
    }
    String h = ESTILO;
    h += "<h1>Reiniciando</h1><p>Volta em alguns segundos. Se o pod nao "
         "reaparecer, desconecte e reconecte a USB dele.</p>"
         "<p><a href='/'>Ir para a pagina de status</a></p>";
    responder(200, h);
    http.client().flush();
    delay(300);
    ESP.restart();
  });
}
