# ⌚ Radar de Preços & Buy-Timing Analytics — Apple Watch SE

Sistema completo de **inteligência de preços, detecção de anomalias promocionais e recomendação estatística do momento ótimo de compra** para a linha **Apple Watch SE** no varejo brasileiro (com foco principal no **Apple Watch SE 2ª Geração GPS 40mm**).

Construído como aplicação prática de compras e case de **Portfólio para Analista de Dados**, unindo modelagem relacional em **SQL (MySQL/MariaDB)**, pipeline **ETL em Python 3.10**, estatística não-paramétrica robusta, sincronização com **Google Sheets**, alertas via **Telegram Bot** e **Dashboard Web próprio**.

---

## 🎯 Problema de Negócio Resolvido

Monitorar apenas "quedas nominais" no varejo eletrônico brasileiro gera falsos positivos por três motivos clássicos:
1. **Poluição de SKU:** anúncios de *pulseiras, cases, aparelhos de vitrine/recondicionados* ou mistura entre gerações (**SE 2 vs. SE 3**) e tamanhos (**40mm vs. 44mm**) contaminam a média histórica.
2. **Efeito "Metade do Dobro":** lojas que elevam artificialmente o preço 7–10 dias antes de grandes datas (como Black Friday) para anunciar descontos aparentes de 15% sobre um valor inflado.
3. **Diferença PIX vs. Parcelado:** descontos reais no Brasil concentram-se no pagamento via PIX à vista.

---

## 🧠 Metodologia Estatística (Score de Oportunidade 0–100)

Em vez de usar média simples e desvio-padrão (sensíveis a outliers), o motor [`src/analytics/buy_signal.py`](src/analytics/buy_signal.py) utiliza **estatística robusta**:

- **Percentil Empírico Histórico (até 35 pts):** posição exata do menor preço PIX atual na distribuição histórica do SKU.
- **Z-Score Robusto via MAD — *Median Absolute Deviation* (até 30 pts):**
  $$Z_{\text{robusto}} = \frac{0.6745 \cdot (P_{\text{atual}} - \text{Mediana})}{\text{MAD}}$$
  Identifica quedas estatisticamente genuínas ($Z \le -1.5$) sem ser distorcido por picos artificiais de preço.
- **Distância ao Preço-Alvo (até 25 pts):** proximidade ou superação da meta definida pelo comprador (`R$ 1.899,00` para o `AW-SE2-40-GPS`).
- **Spread PIX + Penalidade Anti-"Metade do Dobro" (até 10 pts / -20 pts):** audita se o pico de 10 dias da loja excedeu em >8% a mediana de 30 dias sem queda real.

---

## 🗂️ Estrutura do Projeto

```text
monitoramento_precos/
├── sql/
│   ├── 01_schema.sql             # Modelagem Star Schema (dim_produtos, dim_lojas, fato_precos, fato_alertas)
│   ├── 02_views_analiticas.sql   # Views SQL com CTEs e Window Functions (LAG, MEDIAN OVER, RANK)
│   └── 03_queries_portfolio.sql  # Queries analíticas prontas (Auditoria Metade do Dobro, Prêmio Geração/Tamanho)
├── src/
│   ├── etl/
│   │   ├── cleaner.py            # Higienização e inferência de SKU (descarta acessórios/vitrine/Cellular)
│   │   ├── collector.py          # Coleta estruturada (JSON-LD Schema.org / Mercado Livre)
│   │   └── seed_calibration.py   # Série calibrada de 35 dias p/ validação estatística e demonstração
│   ├── analytics/
│   │   └── buy_signal.py         # Cálculo de Percentis, Mediana, MAD Z-Score e Score 0–100
│   ├── alerts/
│   │   ├── telegram_bot.py       # Disparo formatado em HTML + trava anti-spam de 12h
│   │   └── setup_telegram.py     # Assistente automático para descobrir seu TELEGRAM_CHAT_ID
│   ├── api/
│   │   └── server.py             # API FastAPI + exportador de snapshot JSON para GitHub Pages / Vercel
│   └── pipeline.py               # CLI principal do pipeline
├── dashboard/                    # Site analítico interativo (modo ao vivo MySQL + fallback estático p/ portfólio)
└── tests/                        # Testes automatizados com Pytest
```

---

## 🚀 Como Executar Localmente (Windows + XAMPP)

1. **Inicie o MySQL no XAMPP** e crie o ambiente virtual Python 3.10 (caso ainda não exista):
   ```powershell
   py -3.10 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. **Rode o pipeline completo** (calibração + coleta ao vivo + cálculo estatístico + alertas + snapshot):
   ```powershell
   .\.venv\Scripts\python.exe -m src.pipeline --completo
   ```

3. **Suba o Dashboard Web + API Analítica:**
   ```powershell
   .\.venv\Scripts\uvicorn.exe src.api.server:app --host 127.0.0.1 --port 8000
   ```
   Acesse em: **http://127.0.0.1:8000**

4. **Rode os testes unitários:**
   ```powershell
   .\.venv\Scripts\pytest.exe -v
   ```

---

## 📱 Como Ativar os Alertas no Celular (Telegram)

1. Instale o **Telegram** no celular e procure por **`@BotFather`** (conta oficial verificada).
2. Envie `/newbot`, escolha um nome (ex.: `Radar Apple Watch`) e um usuário terminado em `bot`.
3. Copie o **Token** gerado e cole em `TELEGRAM_BOT_TOKEN=` no arquivo `.env`.
4. Abra seu novo bot no Telegram, toque em **Começar** (`/start`) e envie um `"Oi"`.
5. No terminal, execute:
   ```powershell
   .\.venv\Scripts\python.exe -m src.alerts.setup_telegram
   ```
   Ele preencherá automaticamente seu `TELEGRAM_CHAT_ID` no `.env` e enviará uma mensagem de teste pro seu celular!

---

## 🌐 Demonstração Online & Arquitetura 100% Ecossistema Google (Custo Zero)

- **Dashboard Público (Firebase Hosting CDN):** **[https://radar-precos-jean.web.app](https://radar-precos-jean.web.app)**
- **Data Warehouse Analítico (Google BigQuery Sandbox):** [`sql/bigquery_schema_and_views.sql`](sql/bigquery_schema_and_views.sql) — tabelas particionadas por data e clusterizadas por SKU/Loja, com Views em GoogleSQL usando `QUALIFY ROW_NUMBER()` e `PERCENTILE_CONT`.
- **Trigger em Nuvem (Google Apps Script):** [`cloud/apps_script/Code.gs`](cloud/apps_script/Code.gs) — *polling* a cada 15 min com **Change Data Capture (CDC) + Heartbeat de 6h**, Batch Load gratuito (`WRITE_APPEND`) no BigQuery Sandbox, gravação no Google Sheets e disparo com Score Robusto para o Telegram Bot.

Para atualizar o site publicado no Firebase Hosting após novas análises locais:
```powershell
npx -y firebase-tools@latest deploy --only hosting --project radar-precos-jean
```
