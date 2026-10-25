// YPOD - API publica na Cloudflare
// ---------------------------------
//
//   ypod_monitor.py (PC da escola) --POST /api/push--> este Worker --> D1
//   site ypod.ypod-sm.workers.dev --GET  /api/*------> este Worker
//
// Os GET devolvem EXATAMENTE o formato do servidor local (ypod_monitor.py), por
// isso o mesmo dashboard.html funciona nos dois lugares. Se mudar um, mude o
// outro.
//
// Escrita: so o PC, com token (secret INGEST_TOKEN). Leitura: publica.

// Mesma ordem e nomes de KEYS em ypod_monitor.py e das colunas em schema.sql.
// Sao constantes: e seguro montar SQL com elas.
const KEYS = ["temp", "rh", "fig2600", "fig2602", "mox3", "co", "aux", "co2",
              "pm1", "pm25", "pm10", "vento_vel", "vento_dir"];

// Grandezas CIRCULARES: a escala da a volta, entao media aritmetica mente. A
// media de 350 e 10 graus e 180 -- a direcao oposta -- e o erro aparece
// exatamente quando o vento vem do norte. Agrega-se o vetor unitario:
//     angulo = atan2(soma dos senos, soma dos cossenos)
// Minimo e maximo de angulo nao significam nada, entao vao nulos.
const CIRC = ["vento_dir"];
const ehCirc = (k) => CIRC.indexOf(k) >= 0;

function mediaCircular(sen, cos) {
  if (sen === null || cos === null || sen === undefined || cos === undefined) return null;
  if (sen === 0 && cos === 0) return null;        // vetores se cancelaram
  const g = Math.atan2(sen, cos) * 180 / Math.PI;
  return (Math.round(((g % 360) + 360) % 360 * 1000) / 1000) % 360;
}

const MAX_POINTS = 1400;          // pontos por serie, igual ao servidor local
const POR_MINUTO_ATE_S = 2 * 86400;   // janelas maiores leem o resumo por hora
const STALE_S = 60;               // leitura mais velha que isso = "sem dados"
const PC_OFFLINE_S = 120;         // PC sem mandar nada ha isso = offline
const LOTE_MAX = 1000;            // linhas por POST
const EXPORT_MAX = 200000;        // linhas no CSV (~4,5 meses por minuto)
const FONTE = "online · médias por minuto";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
    try {
      if (url.pathname === "/api/push") {
        if (req.method !== "POST") return texto("use POST", 405);
        return await push(req, env);
      }
      if (req.method !== "GET" && req.method !== "HEAD") return texto("somente leitura", 405);
      const q = url.searchParams;
      switch (url.pathname) {
        case "/api/live":   return json(await live(env));
        case "/api/window": return json(await janela(env, q));
        case "/api/range":  return json(faixa(await estado(env)));
        case "/api/table":  return json(await tabela(env, q));
        case "/api/export": return await exportar(env, q);
        case "/":           return texto("YPOD API no ar. O painel fica em https://ypod.ypod-sm.workers.dev");
      }
      return texto("not found", 404);
    } catch (e) {
      return json({ error: String(e && e.message || e) }, 500);
    }
  },
};

// ---------------------------------------------------------------------------
// respostas
// ---------------------------------------------------------------------------
function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...CORS, "Content-Type": "application/json; charset=utf-8",
               "Cache-Control": "no-store" },
  });
}

function texto(s, status = 200) {
  return new Response(s, {
    status, headers: { ...CORS, "Content-Type": "text/plain; charset=utf-8" },
  });
}

// ---------------------------------------------------------------------------
// escrita: o PC empurra as medias pendentes + o retrato "ao vivo"
// ---------------------------------------------------------------------------
async function push(req, env) {
  const auth = req.headers.get("Authorization") || "";
  if (!env.INGEST_TOKEN || !iguais(auth, "Bearer " + env.INGEST_TOKEN)) {
    return texto("token invalido", 401);
  }
  let body;
  try {
    body = await req.json();
  } catch (e) {
    return texto("JSON invalido", 400);
  }
  const rows = Array.isArray(body.rows) ? body.rows : [];
  if (rows.length > LOTE_MAX) return texto("lote grande demais (max " + LOTE_MAX + ")", 413);

  let inseridas = 0;
  if (rows.length) {
    // Um INSERT so para o lote inteiro: o D1 aceita no maximo 100 parametros
    // por consulta, entao em vez de VALUES (?,?,...) x N o lote vai como um
    // unico JSON e o proprio SQLite desmonta com json_each.
    const cols = ["k", "ts", "pod_id", "firmware", ...KEYS];
    const sel = cols.map((c) => "json_extract(value, '$." + c + "')").join(", ");
    const r = await env.DB.prepare(
      "INSERT OR IGNORE INTO leituras (" + cols.join(", ") + ") SELECT " + sel +
      " FROM json_each(?1) WHERE json_extract(value, '$.k') IS NOT NULL" +
      " AND json_extract(value, '$.ts') IS NOT NULL"
    ).bind(JSON.stringify(rows)).run();
    inseridas = (r.meta && r.meta.changes) || 0;

    let lo = Infinity, hi = -Infinity;
    for (const x of rows) {
      if (typeof x.ts === "number") { lo = Math.min(lo, x.ts); hi = Math.max(hi, x.ts); }
    }
    if (inseridas > 0 && Number.isFinite(lo)) {
      await refazerHoras(env, Math.floor(lo / 3600), Math.floor(hi / 3600));
      await env.DB.prepare(
        "UPDATE estado SET rows = rows + ?1," +
        " first = MIN(COALESCE(first, ?2), ?2), last = MAX(COALESCE(last, ?3), ?3)" +
        " WHERE id = 1"
      ).bind(inseridas, lo, hi).run();
    }
  }

  if (body.live && typeof body.live === "object") {
    await env.DB.prepare("UPDATE estado SET live = ?1, live_at = ?2 WHERE id = 1")
      .bind(JSON.stringify(body.live), Date.now()).run();
  }
  return json({ ok: true, recebidas: rows.length, inseridas });
}

async function refazerHoras(env, h0, h1) {
  const cols = KEYS.map((k) => k + "_s, " + k + "_n, " + k + "_lo, " + k + "_hi").join(", ") +
               CIRC.map((k) => ", " + k + "_sen, " + k + "_cos").join("");
  const sel = KEYS.map((k) => "SUM(" + k + "), COUNT(" + k + "), MIN(" + k + "), MAX(" + k + ")").join(", ") +
              CIRC.map((k) => ", SUM(sin(radians(" + k + "))), SUM(cos(radians(" + k + ")))").join("");
  await env.DB.prepare(
    "INSERT OR REPLACE INTO hora (h, n, " + cols + ")" +
    " SELECT CAST(ts / 3600 AS INTEGER), COUNT(*), " + sel +
    " FROM leituras WHERE ts >= ?1 AND ts < ?2 GROUP BY 1"
  ).bind(h0 * 3600, (h1 + 1) * 3600).run();
}

// comparacao sem atalho, para o tempo de resposta nao vazar o token
function iguais(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

// ---------------------------------------------------------------------------
// leitura
// ---------------------------------------------------------------------------
async function estado(env) {
  return await env.DB.prepare("SELECT * FROM estado WHERE id = 1").first();
}

function faixa(e) {
  return { first: e ? e.first : null, last: e ? e.last : null, rows: e ? e.rows : 0 };
}

function metaDoPc(e) {
  try {
    return e && e.live ? JSON.parse(e.live) : {};
  } catch (err) {
    return {};
  }
}

async function live(env) {
  const e = await estado(env);
  const s = metaDoPc(e);
  if (!s.cards) {
    return { status: "aguardando", detail: "o PC da escola ainda não enviou dados",
             source: FONTE, stale: true, age: null, backlog: 0,
             meta: { pod_id: null, firmware: null }, count: 0, bad_lines: 0,
             gravadas: 0, cards: [], server_time: Date.now() };
  }
  // O retrato foi tirado quando o PC mandou; envelhece desde entao.
  const desde = (Date.now() - (e.live_at || 0)) / 1000;
  if (s.age !== null && s.age !== undefined) s.age = Math.round((s.age + desde) * 10) / 10;
  s.stale = s.age === null || s.age === undefined || s.age > STALE_S;
  s.source = FONTE;
  if (desde > PC_OFFLINE_S) {
    s.status = "pc offline";
    s.detail = "O computador da escola não envia dados desde " +
      new Date(e.live_at).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" }) +
      ". O histórico abaixo continua disponível.";
  }
  delete s.fields; delete s.groups; delete s.influx; delete s.nuvem;
  s.server_time = Date.now();
  return s;
}

function lerJanela(q) {
  const num = (nome) => {
    const v = q.get(nome);
    if (!v) return null;
    const n = Number(v);
    if (Number.isFinite(n)) return n / 1000;
    const t = Date.parse(v);
    return Number.isFinite(t) ? t / 1000 : null;
  };
  const t0 = num("from"), t1 = num("to");
  if (t0 !== null && t1 !== null && t1 > t0) return [t0, t1];
  let win = parseInt(q.get("window") || "3600", 10);
  if (!Number.isFinite(win)) win = 3600;
  const agora = Date.now() / 1000;
  return [agora - Math.max(60, win), agora + 1];
}

function baldePara(span) {
  for (const b of [60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400]) {
    if (span / b <= MAX_POINTS) return b;
  }
  return 86400;
}

const r2 = (v) => Math.round(v * 100) / 100;
const r3 = (v) => Math.round(v * 1000) / 1000;

async function janela(env, q) {
  const [t0, t1] = lerJanela(q);
  const e = await estado(env);
  const pc = metaDoPc(e);
  const series = {};
  for (const k of KEYS) series[k] = [];
  let stats = {}, bucket = 60;

  // A escolha segue o trecho que TEM dado, nao o tamanho pedido (igual ao
  // servidor local): "7 dias" com 2 dias gravados merece resolucao de minuto.
  // O resumo por hora responde isso lendo poucas linhas.
  const h0 = Math.floor(t0 / 3600), h1 = Math.ceil(t1 / 3600);
  const extH = await env.DB.prepare(
    "SELECT MIN(h) a, MAX(h) b FROM hora WHERE h >= ?1 AND h < ?2"
  ).bind(h0, h1).first();
  const comDado = extH && extH.a !== null ? (extH.b - extH.a + 1) * 3600 : 0;

  if (comDado <= POR_MINUTO_ATE_S) {
    // --- das medias por minuto
    const ext = await env.DB.prepare(
      "SELECT MIN(ts) a, MAX(ts) b FROM leituras WHERE ts >= ?1 AND ts < ?2"
    ).bind(t0, t1).first();
    if (ext && ext.a !== null) {
      bucket = baldePara(Math.max(ext.b - ext.a, 60));
      const sel = KEYS.map((k) => ehCirc(k)
        ? "SUM(sin(radians(" + k + "))) " + k + "_sen, SUM(cos(radians(" + k + "))) " + k + "_cos"
        : "AVG(" + k + ") " + k).join(", ");
      const { results } = await env.DB.prepare(
        "SELECT CAST(ts / ?3 AS INTEGER) g, " + sel + " FROM leituras" +
        " WHERE ts >= ?1 AND ts < ?2 GROUP BY g ORDER BY g"
      ).bind(t0, t1, bucket).all();
      paraSeries(results, bucket, series);

      const st = KEYS.map((k) => ehCirc(k)
        ? "SUM(sin(radians(" + k + "))) " + k + "_sen, SUM(cos(radians(" + k + "))) " +
          k + "_cos, COUNT(" + k + ") " + k + "_n"
        : "MIN(" + k + ") " + k + "_lo, AVG(" + k + ") " + k + "_m, MAX(" + k + ") " +
          k + "_hi, COUNT(" + k + ") " + k + "_n").join(", ");
      const row = await env.DB.prepare(
        "SELECT " + st + " FROM leituras WHERE ts >= ?1 AND ts < ?2"
      ).bind(t0, t1).first();
      stats = paraStats(row);
    } else {
      stats = paraStats(null);
    }
  } else {
    // --- do resumo por hora
    const ext = extH;
    {
      bucket = Math.max(3600, baldePara(Math.max((ext.b - ext.a) * 3600, 3600)));
      const sel = KEYS.map((k) => ehCirc(k)
        ? "SUM(" + k + "_sen) " + k + "_sen, SUM(" + k + "_cos) " + k + "_cos"
        : "SUM(" + k + "_s) / SUM(" + k + "_n) " + k).join(", ");
      const { results } = await env.DB.prepare(
        "SELECT CAST(h * 3600 / ?3 AS INTEGER) g, " + sel + " FROM hora" +
        " WHERE h >= ?1 AND h < ?2 GROUP BY g ORDER BY g"
      ).bind(h0, h1, bucket).all();
      paraSeries(results, bucket, series);

      const st = KEYS.map((k) => ehCirc(k)
        ? "SUM(" + k + "_sen) " + k + "_sen, SUM(" + k + "_cos) " + k + "_cos, SUM(" +
          k + "_n) " + k + "_n"
        : "MIN(" + k + "_lo) " + k + "_lo, SUM(" + k + "_s) / SUM(" + k + "_n) " + k +
          "_m, MAX(" + k + "_hi) " + k + "_hi, SUM(" + k + "_n) " + k + "_n").join(", ");
      const row = await env.DB.prepare(
        "SELECT " + st + " FROM hora WHERE h >= ?1 AND h < ?2"
      ).bind(h0, h1).first();
      stats = paraStats(row);
    }
  }

  return { from: t0 * 1000, to: t1 * 1000, bucket, stats, series,
           groups: pc.groups || [], fields: pc.fields || [], range: faixa(e) };
}

function paraSeries(results, bucket, series) {
  for (const row of results || []) {
    const t = row.g * bucket * 1000;
    for (const k of KEYS) {
      if (ehCirc(k)) {
        const ang = mediaCircular(row[k + "_sen"], row[k + "_cos"]);
        if (ang !== null) series[k].push([t, ang]);
      } else if (row[k] !== null && row[k] !== undefined) {
        series[k].push([t, r3(row[k])]);
      }
    }
  }
}

function paraStats(row) {
  const out = {};
  for (const k of KEYS) {
    const n = row ? row[k + "_n"] : 0;
    if (ehCirc(k)) {
      const ang = row ? mediaCircular(row[k + "_sen"], row[k + "_cos"]) : null;
      out[k] = (n && ang !== null) ? { min: null, avg: ang, max: null, n } : null;
      continue;
    }
    out[k] = n ? { min: r2(row[k + "_lo"]), avg: r2(row[k + "_m"]),
                   max: r2(row[k + "_hi"]), n } : null;
  }
  return out;
}

async function tabela(env, q) {
  const [t0, t1] = lerJanela(q);
  let limit = parseInt(q.get("limit") || "100", 10);
  if (!Number.isFinite(limit) || limit < 1) limit = 100;
  limit = Math.min(limit, 2000);
  const { results } = await env.DB.prepare(
    "SELECT ts, " + KEYS.join(", ") + " FROM leituras" +
    " WHERE ts >= ?1 AND ts < ?2 ORDER BY ts DESC LIMIT ?3"
  ).bind(t0, t1, limit).all();
  const rows = (results || []).map((r) => {
    const v = {};
    for (const k of KEYS) v[k] = r[k];
    return { t: r.ts * 1000, v };
  });
  const pc = metaDoPc(await estado(env));
  return { rows, keys: pc.fields || KEYS.map((k) => ({ key: k, label: k, unit: "" })) };
}

// "2026-10-24 14:05:30" no horario de Brasilia (o Worker roda em UTC)
const FMT = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const quando = (s) => FMT.format(new Date(s * 1000));

async function exportar(env, q) {
  const [t0, t1fim] = lerJanela(q);
  const t1 = Math.min(t1fim, Date.now() / 1000 + 1);
  const linhas = ["horario_brasilia,pod_id,firmware," + KEYS.join(",")];
  // em paginas, pelo indice de ts, para nao montar um resultado gigante de uma vez
  let desde = t0, total = 0;
  while (total < EXPORT_MAX) {
    const { results } = await env.DB.prepare(
      "SELECT ts, pod_id, firmware, " + KEYS.join(", ") + " FROM leituras" +
      " WHERE ts > ?1 AND ts < ?2 ORDER BY ts LIMIT 10000"
    ).bind(desde === t0 ? t0 - 1e-6 : desde, t1).all();
    if (!results || !results.length) break;
    for (const r of results) {
      linhas.push([quando(r.ts), r.pod_id || "", r.firmware || "",
                   ...KEYS.map((k) => (r[k] === null || r[k] === undefined ? "" : r[k]))].join(","));
    }
    total += results.length;
    desde = results[results.length - 1].ts;
    if (results.length < 10000) break;
  }
  const nome = "ypod_" + quando(t0).slice(0, 16).replace(/[: ]/g, (c) => (c === " " ? "_" : "")) +
               "_a_" + quando(t1).slice(0, 16).replace(/[: ]/g, (c) => (c === " " ? "_" : "")) + ".csv";
  return new Response(linhas.join("\n") + "\n", {
    headers: { ...CORS, "Content-Type": "text/csv; charset=utf-8",
               "Content-Disposition": 'attachment; filename="' + nome + '"',
               "Cache-Control": "no-store" },
  });
}
