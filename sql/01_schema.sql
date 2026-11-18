-- ============================================================================
-- PROJETO: Radar de Preços & Buy-Timing Analytics (Apple Watch SE)
-- BANCO:   MySQL / MariaDB 10.4+ (XAMPP)
-- ARQUIVO: 01_schema.sql (Modelagem Dimensional / Star Schema Simplificado)
-- ============================================================================

CREATE DATABASE IF NOT EXISTS monitoramento_precos
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE monitoramento_precos;

-- 1. Dimensão de Produtos (SKUs monitorados separados por geração/tamanho)
CREATE TABLE IF NOT EXISTS dim_produtos (
    sku                      VARCHAR(32)   NOT NULL PRIMARY KEY,
    nome                     VARCHAR(140)  NOT NULL,
    geracao                  VARCHAR(20)   NOT NULL,
    tamanho_mm               TINYINT UNSIGNED NOT NULL,
    conectividade            VARCHAR(20)   NOT NULL DEFAULT 'GPS',
    favorito                  TINYINT(1)    NOT NULL DEFAULT 0 COMMENT '1 = foco principal de compra',
    ativo                    TINYINT(1)    NOT NULL DEFAULT 1,
    preco_alvo_pix           DECIMAL(10,2) NOT NULL,
    preco_min_valido         DECIMAL(10,2) NOT NULL COMMENT 'Piso anti-ruído (descarta pulseiras/usados)',
    preco_max_valido         DECIMAL(10,2) NOT NULL DEFAULT 6000.00,
    desconto_mediana_gatilho DECIMAL(5,4)  NOT NULL DEFAULT 0.1000 COMMENT 'Ex: 0.1000 = 10% abaixo da mediana',
    termos_obrigatorios      VARCHAR(255)  NOT NULL,
    termos_bloqueados        VARCHAR(500)  NOT NULL,
    criado_em                DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Dimensão de Lojas / Varejistas
CREATE TABLE IF NOT EXISTS dim_lojas (
    id_loja            INT UNSIGNED  NOT NULL AUTO_INCREMENT PRIMARY KEY,
    slug               VARCHAR(40)   NOT NULL UNIQUE,
    nome               VARCHAR(80)   NOT NULL,
    confiabilidade     DECIMAL(3,1)  NOT NULL DEFAULT 9.0 COMMENT 'Nota 0-10 de reputação/entrega',
    desconto_pix_tipico DECIMAL(4,2) NOT NULL DEFAULT 10.00,
    ativa              TINYINT(1)    NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Fontes Monitoradas (URLs diretas ou buscas estruturadas por SKU e Loja)
CREATE TABLE IF NOT EXISTS fontes_monitoradas (
    id_fonte      INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    sku           VARCHAR(32)  NOT NULL,
    id_loja       INT UNSIGNED NOT NULL,
    tipo_coleta   ENUM('MERCADOLIVRE_BUSCA', 'URL_DIRETA_JSONLD', 'MANUAL_SHEETS') NOT NULL,
    url_ou_termo   VARCHAR(600) NOT NULL,
    ativo         TINYINT(1)   NOT NULL DEFAULT 1,
    ultima_coleta DATETIME     NULL,
    ultimo_status VARCHAR(60)  NULL,
    CONSTRAINT fk_fontes_sku  FOREIGN KEY (sku)     REFERENCES dim_produtos(sku) ON UPDATE CASCADE,
    CONSTRAINT fk_fontes_loja FOREIGN KEY (id_loja) REFERENCES dim_lojas(id_loja) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. Tabela Fato de Histórico de Preços
CREATE TABLE IF NOT EXISTS fato_precos (
    id_coleta        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    coletado_em      DATETIME        NOT NULL,
    sku              VARCHAR(32)     NOT NULL,
    id_loja          INT UNSIGNED    NOT NULL,
    titulo_anuncio   VARCHAR(255)    NOT NULL,
    vendedor         VARCHAR(120)    NULL,
    preco_pix        DECIMAL(10,2)   NOT NULL,
    preco_parcelado  DECIMAL(10,2)   NOT NULL,
    max_parcelas     TINYINT UNSIGNED NOT NULL DEFAULT 10,
    spread_pix_pct   DECIMAL(5,2)    GENERATED ALWAYS AS (
        CASE WHEN preco_parcelado > 0
             THEN ROUND(((preco_parcelado - preco_pix) / preco_parcelado) * 100, 2)
             ELSE 0.00
        END
    ) STORED,
    disponivel       TINYINT(1)      NOT NULL DEFAULT 1,
    url_oferta       VARCHAR(700)    NOT NULL,
    origem_dados     ENUM('LIVE', 'HISTORICO_CALIBRACAO') NOT NULL DEFAULT 'LIVE',
    INDEX idx_precos_sku_data (sku, coletado_em),
    INDEX idx_precos_loja_data (id_loja, coletado_em),
    INDEX idx_precos_origem (origem_dados),
    CONSTRAINT fk_precos_sku  FOREIGN KEY (sku)     REFERENCES dim_produtos(sku) ON UPDATE CASCADE,
    CONSTRAINT fk_precos_loja FOREIGN KEY (id_loja) REFERENCES dim_lojas(id_loja) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. Tabela Fato de Alertas Disparados (Controle Anti-Spam & Auditoria de Sinais)
CREATE TABLE IF NOT EXISTS fato_alertas (
    id_alerta          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    disparado_em       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sku                VARCHAR(32)     NOT NULL,
    id_loja            INT UNSIGNED    NOT NULL,
    preco_pix          DECIMAL(10,2)   NOT NULL,
    mediana_referencia DECIMAL(10,2)   NOT NULL,
    minimo_historico   DECIMAL(10,2)   NOT NULL,
    z_score_robusto    DECIMAL(6,2)    NOT NULL,
    percentil_hist     DECIMAL(5,1)    NOT NULL,
    score_oportunidade TINYINT UNSIGNED NOT NULL,
    classificacao      VARCHAR(40)     NOT NULL,
    motivo_gatilho     VARCHAR(255)    NOT NULL,
    canal              VARCHAR(30)     NOT NULL DEFAULT 'TELEGRAM',
    enviado_sucesso    TINYINT(1)      NOT NULL DEFAULT 0,
    url_oferta         VARCHAR(700)    NOT NULL,
    INDEX idx_alertas_sku_data (sku, disparado_em),
    CONSTRAINT fk_alertas_sku  FOREIGN KEY (sku)     REFERENCES dim_produtos(sku) ON UPDATE CASCADE,
    CONSTRAINT fk_alertas_loja FOREIGN KEY (id_loja) REFERENCES dim_lojas(id_loja) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- CARGA INICIAL DAS DIMENSÕES (IDEMPOTENTE)
-- ============================================================================

INSERT INTO dim_produtos (
    sku, nome, geracao, tamanho_mm, conectividade, favorito, ativo,
    preco_alvo_pix, preco_min_valido, preco_max_valido, desconto_mediana_gatilho,
    termos_obrigatorios, termos_bloqueados
) VALUES
(
    'AW-SE2-40-GPS',
    'Apple Watch SE (2ª Geração) GPS 40mm',
    '2ª Geração', 40, 'GPS', 1, 1,
    1899.00, 1300.00, 3200.00, 0.1000,
    'watch,se,40',
    'pulseira,case,capa,pelicula,película,protetor,carregador,cabo,usado,vitrine,recondicionado,swap,seminovo,defeito,caixa vazia,cellular,44mm'
),
(
    'AW-SE2-44-GPS',
    'Apple Watch SE (2ª Geração) GPS 44mm',
    '2ª Geração', 44, 'GPS', 0, 1,
    2099.00, 1450.00, 3500.00, 0.1000,
    'watch,se,44',
    'pulseira,case,capa,pelicula,película,protetor,carregador,cabo,usado,vitrine,recondicionado,swap,seminovo,defeito,caixa vazia,cellular,40mm'
),
(
    'AW-SE3-40-GPS',
    'Apple Watch SE (3ª Geração) GPS 40mm',
    '3ª Geração', 40, 'GPS', 0, 1,
    2499.00, 1850.00, 4200.00, 0.1000,
    'watch,se,40',
    'pulseira,case,capa,pelicula,película,protetor,carregador,cabo,usado,vitrine,recondicionado,swap,seminovo,defeito,caixa vazia,cellular,44mm'
),
(
    'AW-SE3-44-GPS',
    'Apple Watch SE (3ª Geração) GPS 44mm',
    '3ª Geração', 44, 'GPS', 0, 1,
    2699.00, 1950.00, 4500.00, 0.1000,
    'watch,se,44',
    'pulseira,case,capa,pelicula,película,protetor,carregador,cabo,usado,vitrine,recondicionado,swap,seminovo,defeito,caixa vazia,cellular,40mm'
)
ON DUPLICATE KEY UPDATE
    nome = VALUES(nome),
    favorito = VALUES(favorito),
    preco_alvo_pix = VALUES(preco_alvo_pix),
    preco_min_valido = VALUES(preco_min_valido),
    termos_obrigatorios = VALUES(termos_obrigatorios),
    termos_bloqueados = VALUES(termos_bloqueados);

INSERT INTO dim_lojas (slug, nome, confiabilidade, desconto_pix_tipico, ativa) VALUES
('amazon',        'Amazon Brasil',   9.6, 10.00, 1),
('mercadolivre',  'Mercado Livre',   9.3, 10.00, 1),
('kabum',         'KaBuM!',          9.2, 12.00, 1),
('magalu',        'Magazine Luiza',  9.1, 10.00, 1),
('iplace',        'iPlace Oficial',  9.5, 10.00, 1),
('fastshop',      'Fast Shop',       9.4, 11.00, 1)
ON DUPLICATE KEY UPDATE
    nome = VALUES(nome),
    confiabilidade = VALUES(confiabilidade);
