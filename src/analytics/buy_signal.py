"""
Motor Estatístico de Decisão de Compra (Buy-Timing Engine).

Combina estatística não-paramétrica robusta (imune a outliers de 'Metade do Dobro'):
1. Percentil Empírico Histórico (P0..P100)
2. Z-Score Robusto baseado em Mediana e MAD (Median Absolute Deviation)
3. Distância relativa ao Preço-Alvo definido pelo comprador
4. Detector de Inflação Artificial Pré-Promoção ('Metade do Dobro')
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

from src.database import fetch_all


@dataclass(frozen=True)
class BuySignalReport:
    sku: str
    nome_produto: str
    geracao: str
    tamanho_mm: int
    favorito: bool
    id_loja_melhor: int
    melhor_loja: str
    preco_atual_pix: float
    preco_parcelado: float
    spread_pix_pct: float
    preco_alvo_pix: float
    minimo_historico: float
    maximo_historico: float
    p25_historico: float
    mediana_historica: float
    p75_historico: float
    mad_historico: float
    z_score_robusto: float
    percentil_atual: float
    desconto_vs_mediana_pct: float
    alerta_metade_dobro_loja: bool
    score_oportunidade: int
    classificacao: str
    recomendacao_curta: str
    motivos: list[str]
    url_oferta: str
    atualizado_em: str
    total_amostras: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_quantile(sorted_vals: list[float], q: float) -> float:
    """Calcula quantil interpolado linearmente (0.0 <= q <= 1.0) sem depender de pacotes pesados."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def compute_robust_zscore(value: float, series: list[float]) -> tuple[float, float, float]:
    """
    Retorna (mediana, MAD, z_score_robusto).
    Fator 0.6745 torna o MAD consistente com o desvio-padrão sob distribuição normal.
    """
    if not series:
        return 0.0, 0.0, 0.0
    med = float(median(series))
    abs_devs = [abs(x - med) for x in series]
    mad = float(median(abs_devs))
    if mad < 1e-6:
        # Fallback caso todos os preços históricos sejam quase idênticos
        mean_abs = sum(abs_devs) / len(abs_devs) if abs_devs else 1.0
        if mean_abs < 1e-6:
            return med, 0.0, 0.0
        return med, mean_abs, round((value - med) / mean_abs, 2)
    z_rob = 0.6745 * (value - med) / mad
    return med, mad, round(z_rob, 2)


def compute_empirical_percentile(value: float, sorted_vals: list[float]) -> float:
    """Percentual de observações históricas menores que o valor atual (0 = menor da história)."""
    if not sorted_vals:
        return 50.0
    strictly_lower = sum(1 for x in sorted_vals if x < value)
    return round((strictly_lower / len(sorted_vals)) * 100.0, 1)


def score_offer(
    preco_atual: float,
    preco_alvo: float,
    preco_parcelado: float,
    series_historica: list[float],
    store_recent_peak_10d: float | None = None,
    store_30d_median: float | None = None,
) -> dict[str, Any]:
    """Calcula o Score de Oportunidade (0 a 100) e diagnósticos explicáveis."""
    sorted_vals = sorted(series_historica) if series_historica else [preco_atual]
    min_hist = sorted_vals[0]
    max_hist = sorted_vals[-1]
    p25 = round(compute_quantile(sorted_vals, 0.25), 2)
    p75 = round(compute_quantile(sorted_vals, 0.75), 2)
    med, mad, z_rob = compute_robust_zscore(preco_atual, sorted_vals)
    pct_rank = compute_empirical_percentile(preco_atual, sorted_vals)
    desconto_vs_med_pct = round(((med - preco_atual) / med) * 100.0, 2) if med > 0 else 0.0

    # 1. Pontuação por Percentil Histórico (0 a 35 pts)
    if pct_rank <= 5.0:
        pts_percentil = 35.0
    elif pct_rank <= 25.0:
        pts_percentil = 35.0 - ((pct_rank - 5.0) / 20.0) * 10.0  # 25..35
    elif pct_rank <= 50.0:
        pts_percentil = 25.0 - ((pct_rank - 25.0) / 25.0) * 13.0  # 12..25
    else:
        pts_percentil = max(0.0, 12.0 - ((pct_rank - 50.0) / 50.0) * 12.0)

    # 2. Pontuação por Anomalia Estatística — Z-Score Robusto (0 a 30 pts)
    if z_rob <= -2.2:
        pts_z = 30.0
    elif z_rob < 0.0:
        pts_z = (-z_rob / 2.2) * 30.0
    else:
        pts_z = 0.0

    # 3. Pontuação vs Preço-Alvo do Usuário (0 a 25 pts)
    if preco_alvo > 0:
        ratio_alvo = (preco_alvo - preco_atual) / preco_alvo
        if ratio_alvo >= 0:
            # Bateu ou superou a meta do comprador!
            pts_alvo = min(25.0, 20.0 + ratio_alvo * 50.0)
        elif ratio_alvo >= -0.08:
            # Está até 8% acima do alvo
            pts_alvo = max(0.0, 20.0 * (1.0 + ratio_alvo / 0.08))
        else:
            pts_alvo = 0.0
    else:
        pts_alvo = 10.0

    # 4. Eficiência PIX + Auditoria Anti-"Metade do Dobro" (0 a 10 pts, com penalidade se inflado)
    spread_pix = ((preco_parcelado - preco_atual) / preco_parcelado * 100.0) if preco_parcelado > 0 else 0.0
    pts_pix = min(10.0, max(0.0, (spread_pix / 12.0) * 10.0))

    fake_discount = False
    if store_recent_peak_10d and store_30d_median and store_30d_median > 0:
        if store_recent_peak_10d > store_30d_median * 1.08 and preco_atual >= store_30d_median * 0.97:
            fake_discount = True

    raw_score = pts_percentil + pts_z + pts_alvo + pts_pix
    if fake_discount:
        raw_score = max(0.0, raw_score - 20.0)

    final_score = int(round(min(100.0, max(0.0, raw_score))))

    motivos: list[str] = []
    if preco_atual <= min_hist * 1.005:
        motivos.append(f"Menor preço histórico atingido (R$ {preco_atual:,.2f})")
    if preco_atual <= preco_alvo:
        econ_alvo = preco_alvo - preco_atual
        motivos.append(f"Abaixo do preço-alvo de R$ {preco_alvo:,.2f} (-R$ {econ_alvo:,.2f})")
    if desconto_vs_med_pct >= 8.0:
        motivos.append(f"{desconto_vs_med_pct:.1f}% abaixo da mediana histórica (R$ {med:,.2f})")
    if z_rob <= -1.5:
        motivos.append(f"Queda estatística expressiva (Z-Score Robusto = {z_rob:.2f})")
    if fake_discount:
        motivos.append("Alerta: loja inflou preço nos últimos 10 dias (possível 'Metade do Dobro')")
    if not motivos:
        motivos.append(f"Preço dentro da faixa habitual (Percentil P{pct_rank:.0f})")

    if final_score >= 80:
        classificacao = "COMPRA FORTE"
        rec = "COMPRAR AGORA — Janela excelente de preço"
    elif final_score >= 65:
        classificacao = "BOA OFERTA"
        rec = "BOM MOMENTO — Preço descontado frente ao histórico"
    elif final_score >= 40:
        classificacao = "PREÇO NORMAL"
        rec = "AGUARDAR — Espere queda rumo ao P20 ou preço-alvo"
    else:
        classificacao = "CARO / INFLADO"
        rec = "NÃO COMPRAR — Acima da mediana histórica"

    return {
        "min_hist": round(min_hist, 2),
        "max_hist": round(max_hist, 2),
        "p25": p25,
        "mediana": round(med, 2),
        "p75": p75,
        "mad": round(mad, 2),
        "z_robusto": z_rob,
        "percentil": pct_rank,
        "desconto_vs_mediana_pct": desconto_vs_med_pct,
        "fake_discount": fake_discount,
        "score": final_score,
        "classificacao": classificacao,
        "recomendacao": rec,
        "motivos": motivos,
    }


def evaluate_all_skus(origem_filter: str | None = None) -> list[BuySignalReport]:
    """Avalia todos os SKUs ativos no banco MySQL e retorna relatórios ordenados (favorito no topo)."""
    products = fetch_all(
        "SELECT * FROM dim_produtos WHERE ativo = 1 ORDER BY favorito DESC, sku ASC"
    )
    reports: list[BuySignalReport] = []

    for prod in products:
        sku = prod["sku"]
        where_origin = ""
        params: list[Any] = [sku]
        if origem_filter in ("LIVE", "HISTORICO_CALIBRACAO"):
            where_origin = " AND fp.origem_dados = %s"
            params.append(origem_filter)

        hist_rows = fetch_all(
            f"""
            SELECT fp.*, dl.nome AS nome_loja, dl.slug AS loja_slug
              FROM fato_precos fp
              INNER JOIN dim_lojas dl ON dl.id_loja = fp.id_loja
             WHERE fp.sku = %s AND fp.disponivel = 1 {where_origin}
             ORDER BY fp.coletado_em ASC, fp.id_coleta ASC
            """,
            params,
        )
        if not hist_rows:
            continue

        # Última leitura de cada loja para achar o melhor preço vigente
        latest_by_store: dict[int, dict[str, Any]] = {}
        prices_by_store: dict[int, list[float]] = {}
        all_prices: list[float] = []

        for r in hist_rows:
            p = float(r["preco_pix"])
            sid = int(r["id_loja"])
            latest_by_store[sid] = r
            prices_by_store.setdefault(sid, []).append(p)
            all_prices.append(p)

        best_current = min(latest_by_store.values(), key=lambda x: float(x["preco_pix"]))
        best_sid = int(best_current["id_loja"])
        store_series = prices_by_store.get(best_sid, all_prices)
        recent_10 = store_series[-10:] if len(store_series) >= 3 else store_series

        eval_res = score_offer(
            preco_atual=float(best_current["preco_pix"]),
            preco_alvo=float(prod["preco_alvo_pix"]),
            preco_parcelado=float(best_current["preco_parcelado"]),
            series_historica=all_prices,
            store_recent_peak_10d=max(recent_10) if recent_10 else None,
            store_30d_median=float(median(store_series)) if store_series else None,
        )

        reports.append(
            BuySignalReport(
                sku=sku,
                nome_produto=prod["nome"],
                geracao=prod["geracao"],
                tamanho_mm=int(prod["tamanho_mm"]),
                favorito=bool(prod["favorito"]),
                id_loja_melhor=best_sid,
                melhor_loja=best_current["nome_loja"],
                preco_atual_pix=float(best_current["preco_pix"]),
                preco_parcelado=float(best_current["preco_parcelado"]),
                spread_pix_pct=float(best_current["spread_pix_pct"]),
                preco_alvo_pix=float(prod["preco_alvo_pix"]),
                minimo_historico=eval_res["min_hist"],
                maximo_historico=eval_res["max_hist"],
                p25_historico=eval_res["p25"],
                mediana_historica=eval_res["mediana"],
                p75_historico=eval_res["p75"],
                mad_historico=eval_res["mad"],
                z_score_robusto=eval_res["z_robusto"],
                percentil_atual=eval_res["percentil"],
                desconto_vs_mediana_pct=eval_res["desconto_vs_mediana_pct"],
                alerta_metade_dobro_loja=eval_res["fake_discount"],
                score_oportunidade=eval_res["score"],
                classificacao=eval_res["classificacao"],
                recomendacao_curta=eval_res["recomendacao"],
                motivos=eval_res["motivos"],
                url_oferta=best_current["url_oferta"],
                atualizado_em=str(best_current["coletado_em"]),
                total_amostras=len(all_prices),
            )
        )

    return reports
