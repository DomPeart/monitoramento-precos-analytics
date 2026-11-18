-- ============================================================================
-- PROJETO: Radar de Preços & Buy-Timing Analytics (Apple Watch SE)
-- ARQUIVO: 02_views_analiticas.sql (Views com CTEs e Window Functions)
-- ============================================================================

USE monitoramento_precos;

-- 1. View de Histórico Enriquecido com Window Functions:
--    Calcula LAG (preço anterior na mesma loja), Mediana Global do SKU,
--    Mínimo Histórico e Média Móvel das últimas 7 leituras por loja.
CREATE OR REPLACE VIEW vw_historico_enriquecido AS
WITH base AS (
    SELECT
        fp.id_coleta,
        fp.coletado_em,
        fp.sku,
        dp.nome AS nome_produto,
        dp.geracao,
        dp.tamanho_mm,
        dp.favorito,
        dp.preco_alvo_pix,
        dl.id_loja,
        dl.slug AS loja_slug,
        dl.nome AS nome_loja,
        fp.titulo_anuncio,
        fp.preco_pix,
        fp.preco_parcelado,
        fp.max_parcelas,
        fp.spread_pix_pct,
        fp.disponivel,
        fp.url_oferta,
        fp.origem_dados,
        LAG(fp.preco_pix) OVER (
            PARTITION BY fp.sku, fp.id_loja
            ORDER BY fp.coletado_em
        ) AS preco_anterior_loja,
        MIN(fp.preco_pix) OVER (
            PARTITION BY fp.sku
        ) AS minimo_historico_sku,
        MAX(fp.preco_pix) OVER (
            PARTITION BY fp.sku
        ) AS maximo_historico_sku,
        MEDIAN(fp.preco_pix) OVER (
            PARTITION BY fp.sku
        ) AS mediana_historica_sku,
        AVG(fp.preco_pix) OVER (
            PARTITION BY fp.sku, fp.id_loja
            ORDER BY fp.coletado_em
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS media_movel_7_coletas_loja
    FROM fato_precos fp
    INNER JOIN dim_produtos dp ON dp.sku = fp.sku
    INNER JOIN dim_lojas dl    ON dl.id_loja = fp.id_loja
    WHERE fp.disponivel = 1
)
SELECT
    base.*,
    ROUND(base.preco_pix - COALESCE(base.preco_anterior_loja, base.preco_pix), 2) AS variacao_rs_loja,
    CASE
        WHEN base.preco_anterior_loja IS NOT NULL AND base.preco_anterior_loja > 0
        THEN ROUND(((base.preco_pix - base.preco_anterior_loja) / base.preco_anterior_loja) * 100, 2)
        ELSE 0.00
    END AS variacao_pct_loja,
    ROUND(((base.preco_pix - base.mediana_historica_sku) / base.mediana_historica_sku) * 100, 2) AS desvio_vs_mediana_pct
FROM base;

-- 2. View de Ranking Atual por Loja para cada SKU
--    Pega a coleta mais recente de cada par (SKU, Loja) e ranqueia pelo menor PIX.
CREATE OR REPLACE VIEW vw_ranking_atual_lojas AS
WITH ultima_por_loja AS (
    SELECT
        vh.*,
        ROW_NUMBER() OVER (
            PARTITION BY vh.sku, vh.id_loja
            ORDER BY vh.coletado_em DESC, vh.id_coleta DESC
        ) AS rn_recente
    FROM vw_historico_enriquecido vh
)
SELECT
    sku,
    nome_produto,
    favorito,
    id_loja,
    loja_slug,
    nome_loja,
    coletado_em,
    preco_pix,
    preco_parcelado,
    max_parcelas,
    spread_pix_pct,
    preco_alvo_pix,
    minimo_historico_sku,
    ROUND(mediana_historica_sku, 2) AS mediana_historica_sku,
    desvio_vs_mediana_pct,
    RANK() OVER (
        PARTITION BY sku
        ORDER BY preco_pix ASC
    ) AS ranking_melhor_preco,
    ROUND(preco_pix - preco_alvo_pix, 2) AS distancia_preco_alvo_rs,
    titulo_anuncio,
    url_oferta,
    origem_dados
FROM ultima_por_loja
WHERE rn_recente = 1;

-- 3. View Executiva de Decisão de Compra por SKU
CREATE OR REPLACE VIEW vw_kpis_decisao_compra AS
SELECT
    dp.sku,
    dp.nome AS nome_produto,
    dp.geracao,
    dp.tamanho_mm,
    dp.favorito,
    dp.preco_alvo_pix,
    r.nome_loja AS melhor_loja_atual,
    r.preco_pix AS menor_preco_atual_pix,
    r.preco_parcelado AS preco_parcelado_melhor_loja,
    r.spread_pix_pct,
    r.minimo_historico_sku,
    r.mediana_historica_sku,
    r.desvio_vs_mediana_pct,
    r.distancia_preco_alvo_rs,
    r.coletado_em AS atualizado_em,
    r.url_oferta AS link_melhor_oferta,
    (SELECT COUNT(*) FROM fato_precos fp WHERE fp.sku = dp.sku AND fp.disponivel = 1) AS total_observacoes
FROM dim_produtos dp
LEFT JOIN vw_ranking_atual_lojas r
    ON r.sku = dp.sku AND r.ranking_melhor_preco = 1
WHERE dp.ativo = 1;
