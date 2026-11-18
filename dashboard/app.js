const brlFmt = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });

// URL da Implantação Web App do Google Apps Script (conecta o site Firebase direto à Planilha em tempo real)
const CLOUD_WEBAPP_URL =
  (window.RADAR_LOCAL_CONFIG && window.RADAR_LOCAL_CONFIG.CLOUD_WEBAPP_URL) || '';

let appState = {
  payload: null,
  selectedSku: 'AW-SE2-40-GPS',
  isLiveApi: false,
  isCloudLive: false,
  historyChart: null,
  storesChart: null,
};

function formatBRL(val) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return '--';
  return brlFmt.format(Number(val));
}

function getBadgeClass(score) {
  if (score >= 65) return 'pill-green';
  if (score >= 40) return 'pill-amber';
  return 'pill-rose';
}

function getScoreColor(score) {
  if (score >= 80) return '#10b981';
  if (score >= 65) return '#34d399';
  if (score >= 40) return '#f59e0b';
  return '#f43f5e';
}

async function fetchCloudPayload() {
  if (!CLOUD_WEBAPP_URL) return null;
  const cloudRes = await fetch(CLOUD_WEBAPP_URL, {
    method: 'GET',
    credentials: 'omit',
    redirect: 'follow',
  });
  if (!cloudRes.ok) return null;
  const cloudData = await cloudRes.json();
  return cloudData && Array.isArray(cloudData.skus) ? cloudData : null;
}

async function fetchDashboardData() {
  const modeBadge = document.getElementById('dataModeBadge');
  const isLocalHost = ['localhost', '127.0.0.1'].includes(window.location.hostname);

  if (isLocalHost) {
    try {
      const res = await fetch('/api/analytics', { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        appState.isLiveApi = true;
        modeBadge.textContent = '🟢 Conectado ao MySQL (XAMPP)';
        return data;
      }
    } catch (_e) {
      // segue para nuvem ou snapshot
    }
  }

  try {
    const cloudData = await fetchCloudPayload();
    if (cloudData) {
      appState.isCloudLive = true;
      modeBadge.textContent = '☁️ Ao Vivo na Nuvem Google';
      return cloudData;
    }
  } catch (_cloudErr) {
    // fallback para snapshot estático
  }

  const snapRes = await fetch('./data/analytics_snapshot.json', { cache: 'no-store' });
  const data = await snapRes.json();
  appState.isLiveApi = false;
  modeBadge.textContent = '📸 Modo Portfólio Estático (Snapshot)';
  return data;
}

function renderSkuTabs() {
  const container = document.getElementById('skuTabs');
  container.innerHTML = '';
  const skus = appState.payload.skus || [];
  skus.forEach((item) => {
    const btn = document.createElement('button');
    btn.className = `sku-tab ${item.sku === appState.selectedSku ? 'active' : ''}`;
    const star = item.favorito ? '⭐ ' : '';
    btn.textContent = `${star}${item.geracao} • ${item.tamanho_mm}mm (R$ ${Math.round(item.preco_atual_pix)})`;
    btn.addEventListener('click', () => {
      appState.selectedSku = item.sku;
      renderAll();
    });
    container.appendChild(btn);
  });
}

function renderHeroAndKpis(report) {
  const score = report.score_oportunidade;
  const color = getScoreColor(score);
  const deg = Math.round((score / 100) * 360);

  document.getElementById('scoreRing').style.background =
    `conic-gradient(${color} ${deg}deg, #1e293b ${deg}deg)`;
  const scoreEl = document.getElementById('heroScoreVal');
  scoreEl.textContent = score;
  scoreEl.style.color = color;

  const favBadge = document.getElementById('heroFavBadge');
  favBadge.hidden = !report.favorito;

  const classBadge = document.getElementById('heroClassBadge');
  classBadge.textContent = report.classificacao;
  classBadge.className = `status-pill ${getBadgeClass(score)}`;

  document.getElementById('heroProductTitle').textContent = report.nome_produto;
  document.getElementById('heroRecText').textContent = report.recomendacao_curta;

  const ul = document.getElementById('heroReasonsList');
  ul.innerHTML = '';
  (report.motivos || []).forEach((m) => {
    const li = document.createElement('li');
    li.textContent = m;
    ul.appendChild(li);
  });

  document.getElementById('heroBestPrice').textContent = formatBRL(report.preco_atual_pix);
  document.getElementById('heroBestStore').textContent = `na ${report.melhor_loja}`;
  document.getElementById('heroInstallmentInfo').textContent =
    `Parcelado: ${formatBRL(report.preco_parcelado)} (-${report.spread_pix_pct.toFixed(1)}% no PIX)`;
  document.getElementById('heroOfferLink').href = report.url_oferta || '#';

  // KPIs
  document.getElementById('kpiMediana').textContent = formatBRL(report.mediana_historica);
  document.getElementById('kpiIqRange').textContent =
    `Faixa P25–P75: ${formatBRL(report.p25_historico)} a ${formatBRL(report.p75_historico)}`;

  document.getElementById('kpiMinimo').textContent = formatBRL(report.minimo_historico);
  document.getElementById('kpiMaxInfo').textContent =
    `Pico registrado: ${formatBRL(report.maximo_historico)} (${report.total_amostras} leituras)`;

  document.getElementById('kpiPercentil').textContent = `Percentil P${Math.round(report.percentil_atual)}`;
  document.getElementById('kpiZScore').textContent =
    `Z-Score Robusto (MAD): ${report.z_score_robusto > 0 ? '+' : ''}${report.z_score_robusto.toFixed(2)}`;

  document.getElementById('kpiTarget').textContent = formatBRL(report.preco_alvo_pix);
  const diffTarget = report.preco_atual_pix - report.preco_alvo_pix;
  const diffEl = document.getElementById('kpiTargetDiff');
  if (diffTarget <= 0) {
    diffEl.textContent = `✅ ${formatBRL(Math.abs(diffTarget))} abaixo da sua meta!`;
    diffEl.style.color = '#34d399';
  } else {
    diffEl.textContent = `Faltam ${formatBRL(diffTarget)} para bater sua meta`;
    diffEl.style.color = '#94a3b8';
  }
}

function renderHistoryChart(report) {
  const rows = (appState.payload.serie_diaria || []).filter((r) => r.sku === report.sku);
  const dates = [...new Set(rows.map((r) => r.data_ref))].sort();

  // Menor preço do mercado em cada dia + curva da Magalu (para evidenciar metade do dobro quando ocorrer)
  const minByDate = dates.map((d) => {
    const dayRows = rows.filter((r) => r.data_ref === d);
    return Math.min(...dayRows.map((x) => Number(x.menor_pix)));
  });

  const magaluByDate = dates.map((d) => {
    const m = rows.find(
      (r) => r.data_ref === d && (r.loja_slug === 'magalu' || r.loja_slug === 'magazineluiza')
    );
    return m ? Number(m.menor_pix) : null;
  });

  const medianLine = dates.map(() => Number(report.mediana_historica));
  const targetLine = dates.map(() => Number(report.preco_alvo_pix));

  const ctx = document.getElementById('historyChart').getContext('2d');
  if (appState.historyChart) appState.historyChart.destroy();

  appState.historyChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: dates.map((d) => d.slice(5).split('-').reverse().join('/')),
      datasets: [
        {
          label: 'Menor Preço PIX do Dia (Mercado)',
          data: minByDate,
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56, 189, 248, 0.12)',
          borderWidth: 2.6,
          pointRadius: 2.5,
          fill: true,
          tension: 0.25,
        },
        {
          label: 'Mediana Histórica',
          data: medianLine,
          borderColor: '#94a3b8',
          borderDash: [6, 5],
          borderWidth: 1.8,
          pointRadius: 0,
          fill: false,
        },
        {
          label: 'Seu Preço-Alvo (Gatilho)',
          data: targetLine,
          borderColor: '#10b981',
          borderDash: [4, 4],
          borderWidth: 2,
          pointRadius: 0,
          fill: false,
        },
        {
          label: 'Varejista c/ Oscilação Pré-Promo (Auditoria)',
          data: magaluByDate,
          borderColor: 'rgba(244, 63, 94, 0.65)',
          borderWidth: 1.4,
          pointRadius: 0,
          fill: false,
          tension: 0.2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#cbd5e1', boxWidth: 14, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (ctxItem) => `${ctxItem.dataset.label}: ${formatBRL(ctxItem.parsed.y)}`,
          },
        },
      },
      scales: {
        x: { ticks: { color: '#94a3b8', maxTicksLimit: 12 }, grid: { color: 'rgba(38,51,77,0.35)' } },
        y: {
          ticks: {
            color: '#94a3b8',
            callback: (v) => `R$ ${v}`,
          },
          grid: { color: 'rgba(38,51,77,0.35)' },
        },
      },
    },
  });
}

function renderStoresChart(report) {
  const ranking = (appState.payload.ranking_lojas || []).filter((r) => r.sku === report.sku);
  const labels = ranking.map((r) => r.nome_loja);
  const pixVals = ranking.map((r) => Number(r.preco_pix));
  const instVals = ranking.map((r) => Number(r.preco_parcelado));

  const ctx = document.getElementById('storesChart').getContext('2d');
  if (appState.storesChart) appState.storesChart.destroy();

  appState.storesChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Preço PIX à vista',
          data: pixVals,
          backgroundColor: '#10b981',
          borderRadius: 6,
        },
        {
          label: 'Preço Parcelado (10x–12x)',
          data: instVals,
          backgroundColor: '#334155',
          borderRadius: 6,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#cbd5e1', font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (c) => `${c.dataset.label}: ${formatBRL(c.parsed.y)}`,
          },
        },
      },
      scales: {
        x: { ticks: { color: '#cbd5e1', font: { size: 11 } }, grid: { display: false } },
        y: {
          ticks: { color: '#94a3b8', callback: (v) => `R$ ${v}` },
          grid: { color: 'rgba(38,51,77,0.35)' },
        },
      },
    },
  });
}

function renderTables(report) {
  // 1. Auditoria Metade do Dobro para o SKU selecionado
  const auditBody = document.getElementById('auditTableBody');
  auditBody.innerHTML = '';
  const audits = (appState.payload.auditoria_metade_dobro || []).filter((a) => a.sku === report.sku);
  audits.forEach((row) => {
    const tr = document.createElement('tr');
    let pillClass = 'pill-amber';
    if (row.diagnostico.includes('GENUÍNA')) pillClass = 'pill-green';
    if (row.diagnostico.includes('Metade do Dobro')) pillClass = 'pill-rose';

    const realPct = Number(row.desconto_real_pct);
    const realStr = `${realPct > 0 ? '-' : '+'}${Math.abs(realPct).toFixed(1)}%`;

    tr.innerHTML = `
      <td><strong>${row.nome_loja}</strong></td>
      <td class="mono">${formatBRL(row.media_30d)}</td>
      <td class="mono">${formatBRL(row.pico_10d)}</td>
      <td class="mono"><strong>${formatBRL(row.preco_atual)}</strong></td>
      <td class="mono">-${Number(row.desconto_aparente_pct).toFixed(1)}%</td>
      <td class="mono">${realStr}</td>
      <td><span class="status-pill ${pillClass}">${row.diagnostico}</span></td>
    `;
    auditBody.appendChild(tr);
  });

  // 2. Benchmark de Todos os SKUs
  const benchBody = document.getElementById('benchmarkTableBody');
  benchBody.innerHTML = '';
  (appState.payload.skus || []).forEach((s) => {
    const tr = document.createElement('tr');
    if (s.sku === appState.selectedSku) {
      tr.style.background = 'rgba(56, 189, 248, 0.08)';
    }
    const fav = s.favorito ? '⭐ ' : '';
    tr.innerHTML = `
      <td><strong>${fav}${s.geracao} ${s.tamanho_mm}mm</strong></td>
      <td>${s.melhor_loja}</td>
      <td class="mono"><strong>${formatBRL(s.preco_atual_pix)}</strong></td>
      <td class="mono">${formatBRL(s.mediana_historica)}</td>
      <td class="mono">P${Math.round(s.percentil_atual)}</td>
      <td class="mono"><strong>${s.score_oportunidade}/100</strong></td>
      <td><span class="status-pill ${getBadgeClass(s.score_oportunidade)}">${s.classificacao}</span></td>
    `;
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', () => {
      appState.selectedSku = s.sku;
      renderAll();
    });
    benchBody.appendChild(tr);
  });

  // 3. Alertas Recentes
  const alertsBody = document.getElementById('alertsTableBody');
  alertsBody.innerHTML = '';
  const alerts = appState.payload.alertas_recentes || [];
  if (!alerts.length) {
    alertsBody.innerHTML = `<tr><td colspan="8">Nenhum alerta disparado ainda.</td></tr>`;
  } else {
    alerts.forEach((al) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td class="mono">${al.disparado_em}</td>
        <td><strong>${al.sku}</strong></td>
        <td>${al.nome_loja}</td>
        <td class="mono"><strong>${formatBRL(al.preco_pix)}</strong></td>
        <td class="mono">${formatBRL(al.mediana_referencia)}</td>
        <td class="mono"><strong>${al.score_oportunidade}/100</strong></td>
        <td>${al.motivo_gatilho}</td>
        <td><a class="link-sm" href="${al.url_oferta}" target="_blank" rel="noopener">Ver loja ↗</a></td>
      `;
      alertsBody.appendChild(tr);
    });
  }
}

function renderAll() {
  if (!appState.payload || !appState.payload.skus?.length) return;
  const sheetBtn = document.getElementById('sheetLinkBtn');
  const sheetUrl = appState.payload.google_sheet_url || '#';
  sheetBtn.href = sheetUrl;
  sheetBtn.style.display = sheetUrl === '#' ? 'none' : 'inline-flex';

  const tgSub = document.getElementById('telegramStatusSub');
  tgSub.textContent = appState.payload.telegram_configurado
    ? '✅ Bot do Telegram conectado e ativo (trava anti-spam de 12h)'
    : '⚙️ Bot pronto no código — adicione TELEGRAM_BOT_TOKEN no .env após instalar o Telegram no celular';

  renderSkuTabs();
  const report =
    appState.payload.skus.find((s) => s.sku === appState.selectedSku) || appState.payload.skus[0];
  renderHeroAndKpis(report);
  renderHistoryChart(report);
  renderStoresChart(report);
  renderTables(report);
}

document.getElementById('collectNowBtn').addEventListener('click', async () => {
  if (!appState.isLiveApi && !appState.isCloudLive) return;
  const btn = document.getElementById('collectNowBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Atualizando...';
  try {
    if (appState.isLiveApi) {
      await fetch('/api/collect', { method: 'POST' });
    }
    appState.payload = await fetchDashboardData();
    renderAll();
  } finally {
    btn.disabled = false;
    btn.textContent = '🔄 Atualizar Preços';
  }
});

(async function init() {
  const isLocalHost = ['localhost', '127.0.0.1'].includes(window.location.hostname);
  const modeBadge = document.getElementById('dataModeBadge');

  // Renderização instantânea (<100ms) com snapshot CDN enquanto busca dados frescos da nuvem Google
  if (!isLocalHost && CLOUD_WEBAPP_URL) {
    try {
      const snapRes = await fetch('./data/analytics_snapshot.json');
      if (snapRes.ok) {
        appState.payload = await snapRes.json();
        modeBadge.textContent = '⚡ Sincronizando Nuvem Google...';
        renderAll();
      }
    } catch (_e) {
      // ignora se falhar
    }
  }

  appState.payload = await fetchDashboardData();
  renderAll();
})();
