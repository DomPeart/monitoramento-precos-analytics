/**
 * ============================================================================
 * RADAR DE PREÇOS & BUY-TIMING ANALYTICS — MOTOR SERVERLESS (GOOGLE APPS SCRIPT)
 *
 * Arquitetura Profissional 24/7 (Custo R$ 0,00 / Sem Cartão):
 *   1. Polling 24h a cada 15 minutos (inclusive na madrugada, onde surgem bugs
 *      de preço e viradas promocionais).
 *   2. Change Data Capture (CDC) + Heartbeat de 6h:
 *      - Grava IMEDIATAMENTE se o preço mudar (>= R$ 0,50) ou for 1ª leitura;
 *      - Grava 1 confirmação (Heartbeat) a cada 6h se o preço ficar estável;
 *      - Ignora leituras repetidas intermediárias sem poluir o BigQuery/Sheets.
 *   3. Escalonamento Inteligente de Alerta (Dia vs. Madrugada):
 *      - Dia (07h–01h59): envia notificação completa com link no Telegram Bot;
 *      - Madrugada / Sono (02h–06h59): envia o link no chat E FAZ O CELULAR TOCAR
 *        com uma Ligação de Voz no Telegram (TTS em português via CallMeBot)!
 * ============================================================================
 */

const CONFIG_PADRAO = {
  // Defina SPREADSHEET_ID e GCP_PROJECT_ID em Configurações do Projeto > Propriedades do Script
  SPREADSHEET_ID: '',
  GCP_PROJECT_ID: '',
  BIGQUERY_DATASET: 'radar_precos',
  POLLING_MINUTES: 15,
  HEARTBEAT_HOURS: 6,
  MIN_PRICE_DELTA_RS: 0.50,
  MAX_EXECUTION_MS: 270000, // Trava preventiva em 4m30s (abaixo do teto de 6m)
  ALERT_MIN_SCORE: 70,
  ALERT_COOLDOWN_HOURS: 12,
  ALERT_EXTRA_DROP_RS: 30.0,
  SLEEP_START_HOUR: 2, // 02:00 da manhã
  SLEEP_END_HOUR: 6,   // até 06:59 da manhã -> dispara LIGAÇÃO DE VOZ além da mensagem!
};

/**
 * 1. Selecione esta função no topo do Editor e clique em "Executar" UMA ÚNICA VEZ.
 */
function instalarTrigger15Minutos() {
  const triggers = ScriptApp.getProjectTriggers();
  triggers.forEach((t) => {
    if (t.getHandlerFunction() === 'executarCicloMonitoramento') {
      ScriptApp.deleteTrigger(t);
    }
  });

  ScriptApp.newTrigger('executarCicloMonitoramento')
    .timeBased()
    .everyMinutes(CONFIG_PADRAO.POLLING_MINUTES)
    .create();

  Logger.log(
    'Trigger 24/7 instalado: checagem a cada ' +
      CONFIG_PADRAO.POLLING_MINUTES +
      ' minutos (com Despertador por Ligação entre 02h e 06h59).'
  );
}

function instalarTrigger4Horas() {
  instalarTrigger15Minutos();
}

/**
 * Utilitário de teste: selecione `testarLigacaoMadrugada` no topo e clique em Executar
 * para fazer seu celular tocar agora mesmo com uma chamada de voz em português!
 */
function testarLigacaoMadrugada() {
  const props = PropertiesService.getScriptProperties();
  const callUser = (props.getProperty('TELEGRAM_CALL_USER') || '').trim();
  if (!callUser) {
    throw new Error(
      'Defina TELEGRAM_CALL_USER (@seu_usuario) nas Propriedades do Script após enviar /start para @CallMeBot_txtbot no Telegram.'
    );
  }
  const res = dispararLigacaoVozTelegram_(
    callUser,
    'Apple Watch SE 2 40 milimetros',
    1829,
    'KaBuM',
    96
  );
  Logger.log('Status da ligação: ' + JSON.stringify(res));
  if (!res.ok) {
    throw new Error('CallMeBot recusou a chamada: ' + res.detalhe);
  }
}

/**
 * 2. Ciclo Principal executado automaticamente a cada 15 minutos (24h por dia)
 */
function executarCicloMonitoramento() {
  const inicioMs = Date.now();
  const props = PropertiesService.getScriptProperties();
  const sheetId = props.getProperty('SPREADSHEET_ID') || CONFIG_PADRAO.SPREADSHEET_ID;
  const ss = sheetId ? SpreadsheetApp.openById(sheetId) : SpreadsheetApp.getActiveSpreadsheet();

  const abaProdutos = ss.getSheetByName('Produtos');
  const abaFontes = ss.getSheetByName('Fontes');
  const abaHistorico = ss.getSheetByName('Historico');
  const abaAlertas = ss.getSheetByName('Alertas');

  const produtosMap = lerProdutosAtivos_(abaProdutos);
  const fontes = lerFontesAtivas_(abaFontes);
  const ultimoEstadoPorChave = mapearUltimaGravacao_(abaHistorico);

  const novasLinhasSheets = [];
  const novasLinhasBigQuery = [];
  const skusComMudanca = {};

  for (let i = 0; i < fontes.length; i++) {
    if (Date.now() - inicioMs > CONFIG_PADRAO.MAX_EXECUTION_MS) {
      Logger.log('Trava preventiva de 4m30s acionada. Persistindo dados já coletados.');
      break;
    }

    const fonte = fontes[i];
    const prod = produtosMap[fonte.sku];
    if (!prod) continue;

    const resultado = coletarFonteSegura_(fonte, prod);
    const horaCurta = Utilities.formatDate(new Date(), 'America/Sao_Paulo', 'HH:mm');

    if (!resultado.oferta) {
      abaFontes.getRange(fonte.rowIdx, 6).setValue(resultado.status + ' (' + horaCurta + ')');
      continue;
    }

    const of = resultado.oferta;
    const chaveCdc = of.sku + '||' + of.loja.toLowerCase().trim();
    const decisaoCdc = avaliarChangeDataCapture_(ultimoEstadoPorChave[chaveCdc], of.precoPix);

    if (!decisaoCdc.persistir) {
      abaFontes
        .getRange(fonte.rowIdx, 6)
        .setValue('Estável R$ ' + of.precoPix.toFixed(2) + ' (checado ' + horaCurta + ')');
      continue;
    }

    abaFontes
      .getRange(fonte.rowIdx, 6)
      .setValue(decisaoCdc.motivo + ' R$ ' + of.precoPix.toFixed(2) + ' (' + horaCurta + ')');

    const agoraFmt = Utilities.formatDate(new Date(), 'America/Sao_Paulo', 'yyyy-MM-dd HH:mm:ss');
    const agoraIso = new Date().toISOString();

    novasLinhasSheets.push([
      agoraFmt,
      of.sku,
      of.loja,
      of.titulo,
      of.precoPix,
      of.precoParcelado,
      10,
      'SIM',
      decisaoCdc.motivo,
      of.url,
    ]);

    const spreadPct =
      of.precoParcelado > 0
        ? Number((((of.precoParcelado - of.precoPix) / of.precoParcelado) * 100).toFixed(2))
        : 0;

    novasLinhasBigQuery.push(
      JSON.stringify({
        coletado_em: agoraIso,
        sku: of.sku,
        loja_slug: of.loja.toLowerCase().replace(/[^a-z0-9]/g, ''),
        nome_loja: of.loja,
        titulo_anuncio: of.titulo,
        preco_pix: of.precoPix,
        preco_parcelado: of.precoParcelado,
        max_parcelas: 10,
        spread_pix_pct: spreadPct,
        disponivel: true,
        url_oferta: of.url,
        origem_dados: decisaoCdc.motivo,
      })
    );

    skusComMudanca[of.sku] = true;
  }

  if (novasLinhasSheets.length > 0) {
    const startRow = abaHistorico.getLastRow() + 1;
    abaHistorico
      .getRange(startRow, 1, novasLinhasSheets.length, novasLinhasSheets[0].length)
      .setValues(novasLinhasSheets);
    Logger.log('CDC gravou ' + novasLinhasSheets.length + ' nova(s) leitura(s) no Google Sheets.');
  }

  const projectId = props.getProperty('GCP_PROJECT_ID') || CONFIG_PADRAO.GCP_PROJECT_ID;
  if (projectId && novasLinhasBigQuery.length > 0) {
    try {
      carregarBatchNoBigQuerySandbox_(projectId, novasLinhasBigQuery);
    } catch (bqErr) {
      Logger.log('Aviso BigQuery: ' + bqErr);
    }
  }

  if (Object.keys(skusComMudanca).length > 0) {
    CacheService.getScriptCache().remove('DASHBOARD_PAYLOAD_V1');
    avaliarEDispararAlertas_(abaHistorico, abaAlertas, produtosMap, props, skusComMudanca);
  }
}

function avaliarChangeDataCapture_(ultimoRegistro, novoPrecoPix) {
  if (!ultimoRegistro) {
    return { persistir: true, motivo: 'LIVE_INICIAL' };
  }

  const deltaPreco = Math.abs(novoPrecoPix - ultimoRegistro.precoPix);
  if (deltaPreco >= CONFIG_PADRAO.MIN_PRICE_DELTA_RS) {
    const tipo = novoPrecoPix < ultimoRegistro.precoPix ? 'LIVE_QUEDA' : 'LIVE_SUBIDA';
    return { persistir: true, motivo: tipo };
  }

  const horasDesdeUltima = (Date.now() - ultimoRegistro.timestampMs) / (3600 * 1000);
  if (horasDesdeUltima >= CONFIG_PADRAO.HEARTBEAT_HOURS) {
    return { persistir: true, motivo: 'LIVE_HEARTBEAT_6H' };
  }

  return { persistir: false, motivo: 'ESTAVEL_IGNORADO' };
}

function mapearUltimaGravacao_(abaHistorico) {
  const mapa = {};
  const lastRow = abaHistorico.getLastRow();
  if (lastRow < 2) return mapa;

  const qtdLer = Math.min(400, lastRow - 1);
  const startRow = lastRow - qtdLer + 1;
  const rows = abaHistorico.getRange(startRow, 1, qtdLer, 8).getValues();

  rows.forEach((r) => {
    const dh = r[0];
    const sku = String(r[1] || '').trim();
    const loja = String(r[2] || '').toLowerCase().trim();
    const preco = Number(r[4] || 0);
    if (!sku || !loja || preco <= 0) return;

    const ts = dh instanceof Date ? dh.getTime() : new Date(String(dh).replace(' ', 'T')).getTime();
    if (!isNaN(ts)) {
      mapa[sku + '||' + loja] = { precoPix: preco, timestampMs: ts };
    }
  });
  return mapa;
}

function coletarFonteSegura_(fonte, prod) {
  let targetUrl = fonte.url;
  if (!/^https?:\/\//i.test(targetUrl)) {
    const slug = targetUrl.trim().toLowerCase().replace(/\s+/g, '-');
    // Filtro oficial do Mercado Livre para Condição = Novo lacrado (2230284)
    targetUrl = 'https://lista.mercadolivre.com.br/' + slug + '_ITEM*CONDITION_2230284_NoIndex_True';
  }

  try {
    const resp = UrlFetchApp.fetch(targetUrl, {
      method: 'get',
      muteHttpExceptions: true,
      followRedirects: true,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0.0.0 Safari/537.36',
        'Accept-Language': 'pt-BR,pt;q=0.9',
      },
    });

    const code = resp.getResponseCode();
    if (code !== 200) {
      return { status: 'HTTP_' + code, oferta: null };
    }

    const html = resp.getContentText();
    const candidatos = extrairProdutosJsonLd_(html, targetUrl);
    const validos = candidatos.filter((c) => validarAnuncio_(c.titulo, c.precoPix, prod));

    if (validos.length === 0) {
      return { status: 'SEM_ITENS_VALIDOS', oferta: null };
    }

    validos.sort((a, b) => a.precoPix - b.precoPix);
    const melhor = validos[0];
    return {
      status: 'OK',
      oferta: {
        sku: prod.sku,
        loja: fonte.loja,
        titulo: melhor.titulo.substring(0, 240),
        precoPix: melhor.precoPix,
        precoParcelado: melhor.precoParcelado,
        url: melhor.url,
      },
    };
  } catch (err) {
    return { status: 'ERRO: ' + String(err).substring(0, 30), oferta: null };
  }
}

function extrairProdutosJsonLd_(html, fallbackUrl) {
  const regex = /<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  const encontrados = [];
  let match;

  while ((match = regex.exec(html)) !== null) {
    try {
      const parsed = JSON.parse(match[1]);
      const fila = Array.isArray(parsed) ? parsed.slice() : [parsed];
      while (fila.length > 0) {
        const node = fila.shift();
        if (!node || typeof node !== 'object') continue;
        if (Array.isArray(node['@graph'])) fila.push.apply(fila, node['@graph']);
        if (Array.isArray(node.itemListElement)) {
          node.itemListElement.forEach((el) => {
            if (el && typeof el === 'object') fila.push(el.item || el);
          });
        }

        const tipo = String(node['@type'] || '').toLowerCase();
        if (tipo.indexOf('product') !== -1 && node.offers) {
          const offer = Array.isArray(node.offers) ? node.offers[0] : node.offers;
          const p = Number(offer.lowPrice || offer.price || 0);
          if (node.name && p > 0) {
            encontrados.push({
              titulo: String(node.name),
              precoPix: p,
              precoParcelado: Number(offer.highPrice || (p / 0.9).toFixed(2)),
              url: String(offer.url || node.url || fallbackUrl),
            });
          }
        }
      }
    } catch (_e) {
      // Ignora bloco malformado
    }
  }
  return encontrados;
}

function validarAnuncio_(titulo, preco, prod) {
  if (preco < prod.precoMin || preco > 6000) return false;
  const norm = titulo
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();

  if (norm.indexOf('cellular') !== -1 || norm.indexOf('lte') !== -1) return false;
  for (let i = 0; i < prod.bloqueados.length; i++) {
    const b = prod.bloqueados[i];
    if (b && norm.indexOf(b) !== -1) return false;
  }

  const eh40 = /\b40\s*mm\b/.test(norm);
  const eh44 = /\b44\s*mm\b/.test(norm);
  if (prod.sku.indexOf('-40-') !== -1 && !eh40) return false;
  if (prod.sku.indexOf('-44-') !== -1 && !eh44) return false;

  const ehSe3 = /\b(se\s*3|3a\s*geracao|geracao\s*3|s10|2025)\b/.test(norm);
  if (prod.sku.indexOf('SE3') !== -1 && !ehSe3) return false;
  if (prod.sku.indexOf('SE2') !== -1 && ehSe3) return false;

  return true;
}

function carregarBatchNoBigQuerySandbox_(projectId, jsonLinesArray) {
  const dataBlob = Utilities.newBlob(jsonLinesArray.join('\n'), 'application/octet-stream');
  const job = {
    configuration: {
      load: {
        destinationTable: {
          projectId: projectId,
          datasetId: CONFIG_PADRAO.BIGQUERY_DATASET,
          tableId: 'fato_precos',
        },
        sourceFormat: 'NEWLINE_DELIMITED_JSON',
        writeDisposition: 'WRITE_APPEND',
        autodetect: true,
      },
    },
  };
  BigQuery.Jobs.insert(job, projectId, dataBlob);
}

function calcularMediana_(sortedArr) {
  const mid = Math.floor(sortedArr.length / 2);
  return sortedArr.length % 2 !== 0
    ? sortedArr[mid]
    : (sortedArr[mid - 1] + sortedArr[mid]) / 2;
}

/**
 * Faz o celular tocar via chamada de voz gratuita do Telegram (CallMeBot TTS pt-BR)
 */
function dispararLigacaoVozTelegram_(callUser, nomeProduto, precoPix, nomeLoja, score) {
  const textoLimpo = (
    'Atenção! Super oferta detectada, score ' +
    score +
    ' de cem. ' +
    nomeProduto.replace(/[⭐()]/g, '') +
    ' por ' +
    Math.round(precoPix) +
    ' reais no PIX na ' +
    nomeLoja +
    '. O link já está no seu Telegram!'
  ).substring(0, 235);

  const urlCall =
    'https://api.callmebot.com/start.php?user=' +
    encodeURIComponent(callUser) +
    '&text=' +
    encodeURIComponent(textoLimpo) +
    '&lang=pt-BR-Standard-A&rpt=2';

  try {
    const resp = UrlFetchApp.fetch(urlCall, {
      method: 'get',
      muteHttpExceptions: true,
    });
    const body = resp.getContentText() || '';
    if (resp.getResponseCode() !== 200 || body.indexOf('ERROR:') !== -1 || body.indexOf('not authorized') !== -1) {
      const trecho = body.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().substring(0, 180);
      Logger.log('Erro retornado pelo CallMeBot: ' + trecho);
      return { ok: false, detalhe: trecho };
    }
    return { ok: true, detalhe: 'Chamada iniciada com sucesso' };
  } catch (err) {
    Logger.log('Falha ao acionar ligação CallMeBot: ' + err);
    return { ok: false, detalhe: String(err) };
  }
}

function estaEmHorarioDeSono_() {
  const horaBrt = Number(Utilities.formatDate(new Date(), 'America/Sao_Paulo', 'H'));
  return horaBrt >= CONFIG_PADRAO.SLEEP_START_HOUR && horaBrt <= CONFIG_PADRAO.SLEEP_END_HOUR;
}

function avaliarEDispararAlertas_(abaHistorico, abaAlertas, produtosMap, props, skusAlvo) {
  const token = (props.getProperty('TELEGRAM_BOT_TOKEN') || '').trim().replace(/^["']|["']$/g, '');
  const chatId = (props.getProperty('TELEGRAM_CHAT_ID') || '').trim();
  const callUser = (props.getProperty('TELEGRAM_CALL_USER') || '').trim();
  if (abaHistorico.getLastRow() < 2) return;

  const dados = abaHistorico.getRange(2, 1, abaHistorico.getLastRow() - 1, 10).getValues();
  const alertasAntigos =
    abaAlertas.getLastRow() >= 2
      ? abaAlertas.getRange(2, 1, abaAlertas.getLastRow() - 1, 4).getValues()
      : [];

  const madrugada = estaEmHorarioDeSono_();

  Object.keys(skusAlvo).forEach((sku) => {
    const prod = produtosMap[sku];
    if (!prod) return;

    const linhasSku = dados.filter((r) => r[1] === sku && r[7] === 'SIM' && Number(r[4]) > 0);
    if (linhasSku.length === 0) return;

    const sorted = linhasSku.map((r) => Number(r[4])).sort((a, b) => a - b);
    const minHist = sorted[0];
    const mediana = calcularMediana_(sorted);
    const desviosAbs = sorted.map((x) => Math.abs(x - mediana)).sort((a, b) => a - b);
    const mad = calcularMediana_(desviosAbs) || 1;

    const ultima = linhasSku[linhasSku.length - 1];
    const precoAtual = Number(ultima[4]);
    const precoParc = Number(ultima[5] || precoAtual / 0.9);
    const lojaAtual = String(ultima[2]);
    const urlAtual = String(ultima[9]);

    const zRobusto = (0.6745 * (precoAtual - mediana)) / mad;
    const menoresQtd = sorted.filter((x) => x < precoAtual).length;
    const percentil = (menoresQtd / sorted.length) * 100;

    let ptsPct = percentil <= 5 ? 35 : percentil <= 25 ? 35 - ((percentil - 5) / 20) * 10 : Math.max(0, 25 - ((percentil - 25) / 75) * 25);
    let ptsZ = zRobusto <= -2.2 ? 30 : zRobusto < 0 ? (-zRobusto / 2.2) * 30 : 0;
    let ratioAlvo = prod.precoAlvo > 0 ? (prod.precoAlvo - precoAtual) / prod.precoAlvo : 0;
    let ptsAlvo = ratioAlvo >= 0 ? Math.min(25, 20 + ratioAlvo * 50) : ratioAlvo >= -0.08 ? 20 * (1 + ratioAlvo / 0.08) : 0;
    let spread = precoParc > 0 ? ((precoParc - precoAtual) / precoParc) * 100 : 0;
    let ptsPix = Math.min(10, Math.max(0, (spread / 12) * 10));

    const score = Math.round(Math.min(100, Math.max(0, ptsPct + ptsZ + ptsAlvo + ptsPix)));
    if (score < CONFIG_PADRAO.ALERT_MIN_SCORE) return;

    const limiteMs = Date.now() - CONFIG_PADRAO.ALERT_COOLDOWN_HOURS * 3600 * 1000;
    const bloqueadoCooldown = alertasAntigos.some((al) => {
      const dt = al[0] instanceof Date ? al[0].getTime() : new Date(al[0]).getTime();
      return al[1] === sku && dt >= limiteMs && Number(al[3]) - precoAtual < CONFIG_PADRAO.ALERT_EXTRA_DROP_RS;
    });
    if (bloqueadoCooldown) return;

    // 1. Envia sempre a mensagem escrita com o link clicável da oferta
    if (token && chatId) {
      UrlFetchApp.fetch('https://api.telegram.org/bot' + token + '/sendMessage', {
        method: 'post',
        contentType: 'application/json',
        muteHttpExceptions: true,
        payload: JSON.stringify({
          chat_id: chatId,
          parse_mode: 'HTML',
          text:
            '🔥 <b>ALERTA DE OFERTA BOA — SCORE ' + score + '/100</b>\n' +
            (madrugada ? '🌙 <b>[DESPERTADOR DE MADRUGADA ATIVADO]</b>\n' : '') +
            '⌚ <b>' + prod.nome + '</b>\n\n' +
            '🏪 <b>Loja:</b> ' + lojaAtual + '\n' +
            '💸 <b>Preço PIX:</b> R$ ' + precoAtual.toFixed(2) + '\n' +
            '📊 <b>Mediana:</b> R$ ' + mediana.toFixed(2) + ' | <b>Mínimo:</b> R$ ' + minHist.toFixed(2) + '\n' +
            '📐 <b>Estatística:</b> Percentil P' + Math.round(percentil) + ' | Z-Score ' + zRobusto.toFixed(2) + '\n\n' +
            '🔗 <a href="' + urlAtual + '">Abrir Oferta Agora</a>',
        }),
      });
    }

    // 2. Se for entre 02h e 06h59 da manhã, LIGA pro celular via Telegram Voice Call!
    let canalUsado = 'TELEGRAM_MSG';
    if (madrugada && callUser) {
      const resCall = dispararLigacaoVozTelegram_(callUser, prod.nome, precoAtual, lojaAtual, score);
      if (resCall.ok) canalUsado = 'TELEGRAM_MSG + LIGACAO_VOZ';
    }

    const motivo =
      'Score ' + score + '/100 | P' + Math.round(percentil) + ' | Z=' + zRobusto.toFixed(2);
    const agoraFmt = Utilities.formatDate(new Date(), 'America/Sao_Paulo', 'yyyy-MM-dd HH:mm:ss');

    abaAlertas.appendRow([
      agoraFmt,
      sku,
      lojaAtual,
      precoAtual,
      minHist,
      Number(mediana.toFixed(2)),
      motivo,
      canalUsado,
      urlAtual,
    ]);
  });
}

function lerProdutosAtivos_(sheet) {
  const map = {};
  if (sheet.getLastRow() < 2) return map;
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 8).getValues();
  rows.forEach((r) => {
    if (String(r[2]).toUpperCase() === 'SIM') {
      map[String(r[0])] = {
        sku: String(r[0]),
        nome: String(r[1]),
        precoAlvo: Number(r[3]),
        precoMin: Number(r[4]),
        gatilhoDesconto: Number(r[5] || 0.1),
        bloqueados: String(r[7] || '')
          .toLowerCase()
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
      };
    }
  });
  return map;
}

function lerFontesAtivas_(sheet) {
  if (sheet.getLastRow() < 2) return [];
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 6).getValues();
  const list = [];
  rows.forEach((r, idx) => {
    if (String(r[3]).toUpperCase() === 'SIM' && r[4]) {
      list.push({
        rowIdx: idx + 2,
        sku: String(r[1]),
        loja: String(r[2]),
        url: String(r[4]),
      });
    }
  });
  return list;
}

function calcularQuantil_(sortedArr, q) {
  if (!sortedArr.length) return 0;
  if (sortedArr.length === 1) return sortedArr[0];
  const pos = q * (sortedArr.length - 1);
  const lo = Math.floor(pos);
  const hi = Math.min(lo + 1, sortedArr.length - 1);
  const frac = pos - lo;
  return sortedArr[lo] * (1 - frac) + sortedArr[hi] * frac;
}

/**
 * Endpoint público JSON (Web App) que alimenta https://radar-precos-jean.web.app AO VIVO!
 * Calcula todos os KPIs, séries diárias, rankings e auditorias direto da Planilha em nuvem.
 */
function doGet() {
  const cache = CacheService.getScriptCache();
  const cached = cache.get('DASHBOARD_PAYLOAD_V1');
  if (cached) {
    return ContentService.createTextOutput(cached).setMimeType(ContentService.MimeType.JSON);
  }

  const props = PropertiesService.getScriptProperties();
  const sheetId = props.getProperty('SPREADSHEET_ID') || CONFIG_PADRAO.SPREADSHEET_ID;
  const ss = sheetId ? SpreadsheetApp.openById(sheetId) : SpreadsheetApp.getActiveSpreadsheet();

  const abaProdutos = ss.getSheetByName('Produtos');
  const abaHistorico = ss.getSheetByName('Historico');
  const abaAlertas = ss.getSheetByName('Alertas');

  const prodRows = abaProdutos.getLastRow() >= 2
    ? abaProdutos.getRange(2, 1, abaProdutos.getLastRow() - 1, 8).getValues()
    : [];
  const histRows = abaHistorico.getLastRow() >= 2
    ? abaHistorico.getRange(2, 1, abaHistorico.getLastRow() - 1, 10).getValues()
    : [];
  const alertRows = abaAlertas.getLastRow() >= 2
    ? abaAlertas.getRange(2, 1, abaAlertas.getLastRow() - 1, 9).getValues()
    : [];

  const skusOut = [];
  const serieDiariaMap = {};
  const rankingLojasOut = [];
  const auditoriaOut = [];
  const dezDiasAtrasMs = Date.now() - 10 * 86400 * 1000;

  prodRows.forEach((pr, idx) => {
    if (String(pr[2]).toUpperCase() !== 'SIM') return;
    const sku = String(pr[0]).trim();
    const nomeLimpo = String(pr[1]).replace(/^⭐\s*/, '').trim();
    const favorito = idx === 0 || sku === 'AW-SE2-40-GPS';
    const precoAlvo = Number(pr[3] || 0);
    const geracao = sku.indexOf('SE3') !== -1 ? '3ª Geração' : '2ª Geração';
    const tamanhoMm = sku.indexOf('-44-') !== -1 ? 44 : 40;

    const leituras = histRows.filter(
      (r) => String(r[1]).trim() === sku && String(r[7]).toUpperCase() === 'SIM' && Number(r[4]) > 0
    );
    if (!leituras.length) return;

    const todosPrecos = [];
    const ultimaPorLoja = {};
    const precosPorLoja = {};

    leituras.forEach((r) => {
      const dtObj = r[0] instanceof Date ? r[0] : new Date(String(r[0]).replace(' ', 'T'));
      const dataRef = Utilities.formatDate(dtObj, 'America/Sao_Paulo', 'yyyy-MM-dd');
      const lojaNome = String(r[2]).trim();
      const lojaSlug = lojaNome.toLowerCase().replace(/[^a-z0-9]/g, '');
      const pix = Number(r[4]);
      const parc = Number(r[5] || (pix / 0.9).toFixed(2));
      const url = String(r[9] || '');

      todosPrecos.push(pix);
      ultimaPorLoja[lojaNome] = {
        nome_loja: lojaNome,
        loja_slug: lojaSlug,
        preco_pix: pix,
        preco_parcelado: parc,
        url_oferta: url,
        dtMs: dtObj.getTime(),
      };
      if (!precosPorLoja[lojaNome]) precosPorLoja[lojaNome] = [];
      precosPorLoja[lojaNome].push({ pix: pix, dtMs: dtObj.getTime() });

      const serieKey = dataRef + '||' + sku + '||' + lojaSlug;
      if (!serieDiariaMap[serieKey] || pix < serieDiariaMap[serieKey].menor_pix) {
        serieDiariaMap[serieKey] = {
          data_ref: dataRef,
          sku: sku,
          loja_slug: lojaSlug,
          nome_loja: lojaNome,
          menor_pix: pix,
          menor_parcelado: parc,
        };
      }
    });

    const sorted = todosPrecos.slice().sort((a, b) => a - b);
    const minHist = sorted[0];
    const maxHist = sorted[sorted.length - 1];
    const p25 = Number(calcularQuantil_(sorted, 0.25).toFixed(2));
    const mediana = Number(calcularMediana_(sorted).toFixed(2));
    const p75 = Number(calcularQuantil_(sorted, 0.75).toFixed(2));
    const desvios = sorted.map((x) => Math.abs(x - mediana)).sort((a, b) => a - b);
    const mad = calcularMediana_(desvios) || 1;

    const lojasAtuais = Object.keys(ultimaPorLoja)
      .map((k) => ultimaPorLoja[k])
      .sort((a, b) => a.preco_pix - b.preco_pix);

    lojasAtuais.forEach((lj) => {
      rankingLojasOut.push({
        sku: sku,
        nome_loja: lj.nome_loja,
        loja_slug: lj.loja_slug,
        preco_pix: lj.preco_pix,
        preco_parcelado: lj.preco_parcelado,
      });

      const histLoja = precosPorLoja[lj.nome_loja] || [];
      const media30d = histLoja.reduce((acc, x) => acc + x.pix, 0) / histLoja.length;
      const recentes10d = histLoja.filter((x) => x.dtMs >= dezDiasAtrasMs).map((x) => x.pix);
      const pico10d = recentes10d.length ? Math.max.apply(null, recentes10d) : media30d;
      const descAparente = pico10d > 0 ? ((pico10d - lj.preco_pix) / pico10d) * 100 : 0;
      const descReal = media30d > 0 ? ((media30d - lj.preco_pix) / media30d) * 100 : 0;

      let diag = 'OSCILAÇÃO NORMAL';
      if (pico10d > media30d * 1.08 && lj.preco_pix >= media30d * 0.97) {
        diag = 'ALERTA: Possível Metade do Dobro';
      } else if (lj.preco_pix <= media30d * 0.90) {
        diag = 'PROMOÇÃO GENUÍNA';
      }

      auditoriaOut.push({
        sku: sku,
        nome_loja: lj.nome_loja,
        media_30d: Number(media30d.toFixed(2)),
        pico_10d: Number(pico10d.toFixed(2)),
        preco_atual: lj.preco_pix,
        desconto_aparente_pct: Number(descAparente.toFixed(1)),
        desconto_real_pct: Number(descReal.toFixed(1)),
        diagnostico: diag,
      });
    });

    const melhor = lojasAtuais[0];
    const precoAtual = melhor.preco_pix;
    const spreadPix = melhor.preco_parcelado > 0
      ? Number((((melhor.preco_parcelado - precoAtual) / melhor.preco_parcelado) * 100).toFixed(2))
      : 0;
    const zRob = Number(((0.6745 * (precoAtual - mediana)) / mad).toFixed(2));
    const pctAtual = Number(((sorted.filter((x) => x < precoAtual).length / sorted.length) * 100).toFixed(1));
    const descMedianaPct = mediana > 0 ? Number((((mediana - precoAtual) / mediana) * 100).toFixed(1)) : 0;

    let ptsPct = pctAtual <= 5 ? 35 : pctAtual <= 25 ? 35 - ((pctAtual - 5) / 20) * 10 : Math.max(0, 25 - ((pctAtual - 25) / 75) * 25);
    let ptsZ = zRob <= -2.2 ? 30 : zRob < 0 ? (-zRob / 2.2) * 30 : 0;
    let ratioAlvo = precoAlvo > 0 ? (precoAlvo - precoAtual) / precoAlvo : 0;
    let ptsAlvo = ratioAlvo >= 0 ? Math.min(25, 20 + ratioAlvo * 50) : ratioAlvo >= -0.08 ? 20 * (1 + ratioAlvo / 0.08) : 0;
    let ptsPix = Math.min(10, Math.max(0, (spreadPix / 12) * 10));
    const score = Math.round(Math.min(100, Math.max(0, ptsPct + ptsZ + ptsAlvo + ptsPix)));

    const motivos = [];
    if (precoAtual <= minHist * 1.005) motivos.push('Menor preço histórico atingido (R$ ' + precoAtual.toFixed(2) + ')');
    if (precoAtual <= precoAlvo) motivos.push('Abaixo do seu preço-alvo de R$ ' + precoAlvo.toFixed(2));
    if (descMedianaPct >= 8) motivos.push(descMedianaPct + '% abaixo da mediana histórica (R$ ' + mediana.toFixed(2) + ')');
    if (zRob <= -1.5) motivos.push('Queda estatística expressiva (Z-Score Robusto = ' + zRob + ')');
    if (!motivos.length) motivos.push('Preço dentro da faixa habitual (Percentil P' + Math.round(pctAtual) + ')');

    let classificacao = 'CARO / INFLADO';
    let rec = 'NÃO COMPRAR — Acima da mediana histórica';
    if (score >= 80) {
      classificacao = 'COMPRA FORTE';
      rec = 'COMPRAR AGORA — Janela excelente de preço';
    } else if (score >= 65) {
      classificacao = 'BOA OFERTA';
      rec = 'BOM MOMENTO — Preço descontado frente ao histórico';
    } else if (score >= 40) {
      classificacao = 'PREÇO NORMAL';
      rec = 'AGUARDAR — Espere queda rumo ao P20 ou preço-alvo';
    }

    skusOut.push({
      sku: sku,
      nome_produto: nomeLimpo,
      geracao: geracao,
      tamanho_mm: tamanhoMm,
      favorito: favorito,
      melhor_loja: melhor.nome_loja,
      preco_atual_pix: precoAtual,
      preco_parcelado: melhor.preco_parcelado,
      spread_pix_pct: spreadPix,
      preco_alvo_pix: precoAlvo,
      minimo_historico: minHist,
      maximo_historico: maxHist,
      p25_historico: p25,
      mediana_historica: mediana,
      p75_historico: p75,
      z_score_robusto: zRob,
      percentil_atual: pctAtual,
      score_oportunidade: score,
      classificacao: classificacao,
      recomendacao_curta: rec,
      motivos: motivos,
      url_oferta: melhor.url_oferta,
      total_amostras: sorted.length,
    });
  });

  const alertasRecentes = alertRows
    .slice(-20)
    .reverse()
    .map((al) => {
      const dhFmt = al[0] instanceof Date
        ? Utilities.formatDate(al[0], 'America/Sao_Paulo', 'yyyy-MM-dd HH:mm:ss')
        : String(al[0]);
      const matchScore = String(al[6] || '').match(/Score\s+(\d+)/i);
      return {
        disparado_em: dhFmt,
        sku: String(al[1]),
        nome_loja: String(al[2]),
        preco_pix: Number(al[3]),
        mediana_referencia: Number(al[5]),
        score_oportunidade: matchScore ? Number(matchScore[1]) : 90,
        motivo_gatilho: String(al[6]),
        url_oferta: String(al[8]),
      };
    });

  const payloadStr = JSON.stringify({
    gerado_em: Utilities.formatDate(new Date(), 'America/Sao_Paulo', 'yyyy-MM-dd HH:mm:ss'),
    google_sheet_url: '#',
    telegram_configurado: Boolean(props.getProperty('TELEGRAM_BOT_TOKEN')),
    skus: skusOut,
    serie_diaria: Object.keys(serieDiariaMap)
      .map((k) => serieDiariaMap[k])
      .sort((a, b) => (a.data_ref > b.data_ref ? 1 : -1)),
    ranking_lojas: rankingLojasOut,
    auditoria_metade_dobro: auditoriaOut,
    alertas_recentes: alertasRecentes,
  });

  cache.put('DASHBOARD_PAYLOAD_V1', payloadStr, 60);
  return ContentService.createTextOutput(payloadStr).setMimeType(ContentService.MimeType.JSON);
}
