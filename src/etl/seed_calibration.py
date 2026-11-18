"""
Gerador de série histórica calibrada (35 dias) claramente rotulada como 'HISTORICO_CALIBRACAO'
para validação estatística imediata (Z-Score Robusto, Percentis e detecção de 'Metade do Dobro')
e demonstração rica de portfólio. Pode ser removida a qualquer momento sem afetar coletas 'LIVE'.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from src.database import db_cursor, fetch_all


def seed_calibration_history(days: int = 35) -> int:
    """Popula série histórica calibrada se ainda não houver registros de calibração."""
    existing = fetch_all(
        "SELECT COUNT(*) AS qtd FROM fato_precos WHERE origem_dados = 'HISTORICO_CALIBRACAO'"
    )
    if existing and int(existing[0]["qtd"]) > 0:
        return 0

    stores = {
        row["slug"]: int(row["id_loja"])
        for row in fetch_all("SELECT id_loja, slug FROM dim_lojas WHERE ativa = 1")
    }

    # Parâmetros realistas de mercado no varejo brasileiro (Nov/2026)
    sku_profiles = {
        "AW-SE2-40-GPS": {
            "title": "Apple Watch SE (2ª Geração) GPS 40mm Caixa Alumínio Meia-noite",
            "base_pix": 2149.0,
            "spread_factor": 1.11,
        },
        "AW-SE2-44-GPS": {
            "title": "Apple Watch SE (2ª Geração) GPS 44mm Caixa Alumínio Estelar",
            "base_pix": 2369.0,
            "spread_factor": 1.11,
        },
        "AW-SE3-40-GPS": {
            "title": "Apple Watch SE (3ª Geração) GPS 40mm Tela Always-On Chip S10",
            "base_pix": 2849.0,
            "spread_factor": 1.10,
        },
        "AW-SE3-44-GPS": {
            "title": "Apple Watch SE (3ª Geração) GPS 44mm Tela Always-On Chip S10",
            "base_pix": 3099.0,
            "spread_factor": 1.10,
        },
    }

    store_Multipliers = {
        "amazon":       (0.985, 1.10, "https://www.amazon.com.br/s?k=apple+watch+se"),
        "mercadolivre": (0.975, 1.08, "https://lista.mercadolivre.com.br/apple-watch-se-gps"),
        "kabum":        (0.970, 1.13, "https://www.kabum.com.br/busca/apple-watch-se"),
        "magalu":       (1.005, 1.11, "https://www.magazineluiza.com.br/busca/apple+watch+se/"),
        "iplace":       (1.035, 1.10, "https://www.iplace.com.br/apple-watch/apple-watch-se"),
        "fastshop":     (1.015, 1.12, "https://site.fastshop.com.br/apple-watch-se"),
    }

    rng = random.Random(20261117)
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    rows_to_insert: list[tuple] = []

    for day_offset in range(days, -1, -1):
        ts = now - timedelta(days=day_offset)
        days_ago = day_offset

        for sku, prof in sku_profiles.items():
            base = prof["base_pix"]

            for slug, (store_mult, inst_mult, url) in store_Multipliers.items():
                if slug not in stores:
                    continue

                noise = rng.uniform(-0.022, 0.022)
                pix = base * store_mult * (1.0 + noise)

                # Cenário 1: "Metade do Dobro" clássico pré-Black Friday na Magalu (infla entre D-9 e D-3)
                if slug == "magalu" and 3 <= days_ago <= 9:
                    pix = base * 1.135
                elif slug == "magalu" and days_ago < 3:
                    pix = base * 1.01  # Cai do pico inflado, mas continua acima da mediana!

                # Cenário 2: Promoção relâmpago passada (há 19 dias na Amazon)
                if slug == "amazon" and days_ago == 19:
                    pix = base * 0.875

                # Cenário 3: Oferta excelente HOJE no favorito AW-SE2-40-GPS na KaBuM! (Momento de Compra!)
                if sku == "AW-SE2-40-GPS" and slug == "kabum" and days_ago == 0:
                    pix = 1829.00

                # Cenário 4: Preço competitivo hoje na Amazon para AW-SE2-40-GPS
                if sku == "AW-SE2-40-GPS" and slug == "amazon" and days_ago == 0:
                    pix = 1889.00

                pix_rounded = round(pix, 2)
                parcelado_rounded = round(pix_rounded * inst_mult, 2)

                rows_to_insert.append(
                    (
                        ts.strftime("%Y-%m-%d %H:%M:%S"),
                        sku,
                        stores[slug],
                        prof["title"],
                        slug.upper(),
                        pix_rounded,
                        parcelado_rounded,
                        10 if slug != "amazon" else 12,
                        1,
                        url,
                        "HISTORICO_CALIBRACAO",
                    )
                )

    with db_cursor(commit=True) as cur:
        cur.executemany(
            """
            INSERT INTO fato_precos (
                coletado_em, sku, id_loja, titulo_anuncio, vendedor,
                preco_pix, preco_parcelado, max_parcelas, disponivel,
                url_oferta, origem_dados
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows_to_insert,
        )
    return len(rows_to_insert)


def clear_calibration_history() -> int:
    """Remove apenas os dados de calibração, preservando 100% das coletas LIVE."""
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM fato_precos WHERE origem_dados = 'HISTORICO_CALIBRACAO'")
        return cur.rowcount
