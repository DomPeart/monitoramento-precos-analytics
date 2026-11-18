-- ============================================================================
-- PROJETO: Radar de Preços & Buy-Timing Analytics (Apple Watch SE)
-- PLATAFORMA: Google BigQuery (GoogleSQL — Compatível com Sandbox / Free Tier)
-- DESTAQUES PORTFÓLIO: Partitioning, Clustering, QUALIFY e PERCENTILE_CONT
-- ============================================================================

-- Substitua `SEU_PROJECT_ID` pelo ID do seu projeto Google Cloud / Firebase.

CREATE SCHEMA IF NOT EXISTS `SEU_PROJECT_ID.radar_precos`
OPTIONS (
  location = 'southamerica-east1',
  default_table_expiration_days = 60,
  default_partition_expiration_days = 60,
  description = 'Data Warehouse de monitoramento de preços e decisão estatística de compra (Sandbox Ready)'
);

-- 1. Dimensão de Produtos
CREATE TABLE IF NOT EXISTS `SEU_PROJECT_ID.radar_precos.dim_produtos` (
  sku                      STRING    NOT NULL,
  nome                     STRING    NOT NULL,
  geracao                  STRING    NOT NULL,
  tamanho_mm               INT64     NOT NULL,
  conectividade            STRING    NOT NULL,
  favorito                 BOOL      NOT NULL,
  ativo                    BOOL      NOT NULL,
  preco_alvo_pix           NUMERIC   NOT NULL,
  preco_min_valido         NUMERIC   NOT NULL,
  preco_max_valido         NUMERIC   NOT NULL,
  desconto_mediana_gatilho FLOAT64   NOT NULL,
  termos_obrigatorios      STRING    NOT NULL,
  termos_bloqueados        STRING    NOT NULL,
  criado_em                TIMESTAMP NOT NULL
);

-- 2. Tabela Fato Particionada por Dia e Clusterizada por SKU + Loja (Custo ~Zero no BigQuery)
CREATE TABLE IF NOT EXISTS `SEU_PROJECT_ID.radar_precos.fato_precos` (
  coletado_em     TIMESTAMP NOT NULL,
  sku             STRING    NOT NULL,
  loja_slug       STRING    NOT NULL,
  nome_loja       STRING    NOT NULL,
  titulo_anuncio  STRING    NOT NULL,
  preco_pix       NUMERIC   NOT NULL,
  preco_parcelado NUMERIC   NOT NULL,
  max_parcelas    INT64     NOT NULL,
  spread_pix_pct  FLOAT64   NOT NULL,
  disponivel      BOOL      NOT NULL,
  url_oferta      STRING    NOT NULL,
  origem_dados    STRING    NOT NULL
)
PARTITION BY DATE(coletado_em)
CLUSTER BY sku, loja_slug;

-- 3. Tabela Fato de Alertas Disparados (Auditoria e Anti-Spam)
CREATE TABLE IF NOT EXISTS `SEU_PROJECT_ID.radar_precos.fato_alertas` (
  disparado_em       TIMESTAMP NOT NULL,
  sku                STRING    NOT NULL,
  nome_loja          STRING    NOT NULL,
  preco_pix          NUMERIC   NOT NULL,
  mediana_referencia NUMERIC   NOT NULL,
  minimo_historico   NUMERIC   NOT NULL,
  z_score_robusto    FLOAT64   NOT NULL,
  percentil_hist     FLOAT64   NOT NULL,
  score_oportunidade INT64     NOT NULL,
  classificacao      STRING    NOT NULL,
  motivo_gatilho     STRING    NOT NULL,
  canal              STRING    NOT NULL,
  enviado_sucesso    BOOL      NOT NULL,
  url_oferta         STRING    NOT NULL
)
PARTITION BY DATE(disparado_em)
CLUSTER BY sku;

-- ============================================================================
-- VIEW ANALÍTICA 1: Ranking Atual por Loja usando QUALIFY e PERCENTILE_CONT
-- ============================================================================
CREATE OR REPLACE VIEW `SEU_PROJECT_ID.radar_precos.vw_ranking_atual_lojas` AS
WITH historico_com_metricas AS (
  SELECT
    fp.*,
    MIN(fp.preco_pix) OVER (PARTITION BY fp.sku) AS minimo_historico_sku,
    PERCENTILE_CONT(fp.preco_pix, 0.5) OVER (PARTITION BY fp.sku) AS mediana_historica_sku,
    PERCENTILE_CONT(fp.preco_pix, 0.25) OVER (PARTITION BY fp.sku) AS p25_sku,
    PERCENTILE_CONT(fp.preco_pix, 0.75) OVER (PARTITION BY fp.sku) AS p75_sku,
    LAG(fp.preco_pix) OVER (
      PARTITION BY fp.sku, fp.loja_slug
      ORDER BY fp.coletado_em
    ) AS preco_anterior_loja
  FROM `SEU_PROJECT_ID.radar_precos.fato_precos` fp
  WHERE fp.disponivel = TRUE
),
ultimas_leituras AS (
  SELECT *
  FROM historico_com_metricas
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY sku, loja_slug
    ORDER BY coletado_em DESC
  ) = 1
)
SELECT
  u.sku,
  dp.nome AS nome_produto,
  dp.favorito,
  u.loja_slug,
  u.nome_loja,
  u.coletado_em,
  u.preco_pix,
  u.preco_parcelado,
  u.spread_pix_pct,
  dp.preco_alvo_pix,
  u.minimo_historico_sku,
  ROUND(u.mediana_historica_sku, 2) AS mediana_historica_sku,
  ROUND(u.p25_sku, 2) AS p25_sku,
  ROUND(u.p75_sku, 2) AS p75_sku,
  RANK() OVER (PARTITION BY u.sku ORDER BY u.preco_pix ASC) AS ranking_melhor_preco,
  u.url_oferta
FROM ultimas_leituras u
INNER JOIN `SEU_PROJECT_ID.radar_precos.dim_produtos` dp
  ON dp.sku = u.sku;
