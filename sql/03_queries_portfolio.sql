-- ============================================================================
-- PROJETO: Radar de Preços & Buy-Timing Analytics (Apple Watch SE)
-- ARQUIVO: 03_queries_portfolio.sql
-- OBJETIVO: Consultas analíticas avançadas para demonstração em Portfólio
--           de Analista de Dados (Detecção de "Metade do Dobro", Elasticidade
--           de Geração/Tamanho e Eficiência do Pagamento via PIX).
-- ============================================================================

USE monitoramento_precos;

-- ----------------------------------------------------------------------------
-- QUERY 1: Auditoria Anti-"Metade do Dobro" (Pré-Black Friday)
-- Identifica lojas cujo preço máximo nos últimos 10 dias subiu mais de 10%
-- acima da mediana histórica antes de anunciar uma suposta "queda".
-- ----------------------------------------------------------------------------
WITH janela_recente AS (
    SELECT
        fp.sku,
        dl.nome AS nome_loja,
        MAX(CASE WHEN fp.coletado_em >= DATE_SUB(NOW(), INTERVAL 10 DAY) THEN fp.preco_pix END) AS pico_10d,
        SUBSTRING_INDEX(
            GROUP_CONCAT(fp.preco_pix ORDER BY fp.coletado_em DESC SEPARATOR ','),
            ',', 1
        ) + 0.0 AS preco_atual,
        AVG(fp.preco_pix) AS media_loja_30d
    FROM fato_precos fp
    INNER JOIN dim_lojas dl ON dl.id_loja = fp.id_loja
    WHERE fp.disponivel = 1
    GROUP BY fp.sku, dl.nome
)
SELECT
    jr.sku,
    jr.nome_loja,
    ROUND(jr.media_loja_30d, 2) AS media_30d,
    jr.pico_10d,
    jr.preco_atual,
    ROUND(((jr.pico_10d - jr.preco_atual) / jr.pico_10d) * 100, 1) AS desconto_aparente_pct,
    ROUND(((jr.media_loja_30d - jr.preco_atual) / jr.media_loja_30d) * 100, 1) AS desconto_real_vs_media_pct,
    CASE
        WHEN jr.pico_10d > jr.media_loja_30d * 1.08 AND jr.preco_atual >= jr.media_loja_30d * 0.97
        THEN 'ALERTA: Possível Metade do Dobro'
        WHEN jr.preco_atual <= jr.media_loja_30d * 0.90
        THEN 'PROMOÇÃO GENUÍNA'
        ELSE 'OSCILAÇÃO NORMAL'
    END AS diagnostico_preco
FROM janela_recente jr
ORDER BY jr.sku, jr.preco_atual ASC;

-- ----------------------------------------------------------------------------
-- QUERY 2: Análise de Prêmio de Geração (SE 3 vs SE 2) e Tamanho (44mm vs 40mm)
-- Responde: "Quanto custa em % subir da 2ª para a 3ª geração ou de 40mm p/ 44mm?"
-- ----------------------------------------------------------------------------
WITH resumo_modelos AS (
    SELECT
        dp.geracao,
        dp.tamanho_mm,
        ROUND(AVG(fp.preco_pix), 2) AS ticket_medio_pix,
        MIN(fp.preco_pix) AS menor_pix_registrado
    FROM fato_precos fp
    INNER JOIN dim_produtos dp ON dp.sku = fp.sku
    WHERE fp.disponivel = 1
    GROUP BY dp.geracao, dp.tamanho_mm
)
SELECT
    geracao,
    tamanho_mm,
    ticket_medio_pix,
    menor_pix_registrado,
    ROUND(
        ((ticket_medio_pix - MIN(ticket_medio_pix) OVER (PARTITION BY tamanho_mm))
        / MIN(ticket_medio_pix) OVER (PARTITION BY tamanho_mm)) * 100, 2
    ) AS premio_geracao_pct,
    ROUND(
        ((ticket_medio_pix - MIN(ticket_medio_pix) OVER (PARTITION BY geracao))
        / MIN(ticket_medio_pix) OVER (PARTITION BY geracao)) * 100, 2
    ) AS premio_tamanho_44mm_pct
FROM resumo_modelos
ORDER BY geracao, tamanho_mm;

-- ----------------------------------------------------------------------------
-- QUERY 3: Economia Real no PIX vs. Parcelado por Varejista (Foco SE2 40mm)
-- ----------------------------------------------------------------------------
SELECT
    dl.nome AS varejista,
    COUNT(*) AS leituras,
    ROUND(AVG(fp.preco_pix), 2) AS media_pix,
    ROUND(AVG(fp.preco_parcelado), 2) AS media_parcelado,
    ROUND(AVG(fp.preco_parcelado - fp.preco_pix), 2) AS economia_media_rs,
    ROUND(AVG(fp.spread_pix_pct), 2) AS desconto_medio_pix_pct
FROM fato_precos fp
INNER JOIN dim_lojas dl ON dl.id_loja = fp.id_loja
WHERE fp.sku = 'AW-SE2-40-GPS'
  AND fp.disponivel = 1
GROUP BY dl.nome
ORDER BY media_pix ASC;
