"""
Servidor FastAPI para o Dashboard Analítico + Exportador de Snapshot Estático (Portfólio).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.analytics.buy_signal import evaluate_all_skus
from src.alerts.telegram_bot import process_alerts
from src.config import BASE_DIR, load_settings
from src.database import db_cursor, fetch_all
from src.etl.collector import run_live_collection

DASHBOARD_DIR = BASE_DIR / "dashboard"
SNAPSHOT_PATH = DASHBOARD_DIR / "data" / "analytics_snapshot.json"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    return str(obj)


def build_analytics_payload(origem_filter: str | None = None) -> dict[str, Any]:
    """Constrói payload analítico completo a partir do MySQL + Views + Buy-Timing Engine."""
    reports = [r.to_dict() for r in evaluate_all_skus(origem_filter=origem_filter)]

    where_origin = ""
    params: list[Any] = []
    if origem_filter in ("LIVE", "HISTORICO_CALIBRACAO"):
        where_origin = " AND fp.origem_dados = %s"
        params.append(origem_filter)

    # Série temporal diária (menor PIX por dia e loja para cada SKU)
    series_rows = fetch_all(
        f"""
        SELECT
            DATE_FORMAT(fp.coletado_em, '%%Y-%%m-%%d') AS data_ref,
            fp.sku,
            dl.slug AS loja_slug,
            dl.nome AS nome_loja,
            MIN(fp.preco_pix) AS menor_pix,
            MIN(fp.preco_parcelado) AS menor_parcelado,
            fp.origem_dados
        FROM fato_precos fp
        INNER JOIN dim_lojas dl ON dl.id_loja = fp.id_loja
        WHERE fp.disponivel = 1 {where_origin}
        GROUP BY DATE_FORMAT(fp.coletado_em, '%%Y-%%m-%%d'), fp.sku, dl.slug, dl.nome, fp.origem_dados
        ORDER BY data_ref ASC
        """,
        params,
    )

    # Ranking atual por loja (View SQL vw_ranking_atual_lojas)
    ranking_rows = fetch_all(
        """
        SELECT
            sku, nome_loja, loja_slug, preco_pix, preco_parcelado,
            spread_pix_pct, desvio_vs_mediana_pct, ranking_melhor_preco,
            distancia_preco_alvo_rs, coletado_em, url_oferta, origem_dados
        FROM vw_ranking_atual_lojas
        ORDER BY sku ASC, ranking_melhor_preco ASC
        """
    )

    # Auditoria Anti-"Metade do Dobro" via SQL
    audit_rows = fetch_all(
        f"""
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
            WHERE fp.disponivel = 1 {where_origin}
            GROUP BY fp.sku, dl.nome
        )
        SELECT
            jr.sku,
            jr.nome_loja,
            ROUND(jr.media_loja_30d, 2) AS media_30d,
            jr.pico_10d,
            jr.preco_atual,
            ROUND(((jr.pico_10d - jr.preco_atual) / jr.pico_10d) * 100, 1) AS desconto_aparente_pct,
            ROUND(((jr.media_loja_30d - jr.preco_atual) / jr.media_loja_30d) * 100, 1) AS desconto_real_pct,
            CASE
                WHEN jr.pico_10d > jr.media_loja_30d * 1.08 AND jr.preco_atual >= jr.media_loja_30d * 0.97
                THEN 'ALERTA: Possível Metade do Dobro'
                WHEN jr.preco_atual <= jr.media_loja_30d * 0.90
                THEN 'PROMOÇÃO GENUÍNA'
                ELSE 'OSCILAÇÃO NORMAL'
            END AS diagnostico
        FROM janela_recente jr
        ORDER BY jr.sku ASC, jr.preco_atual ASC
        """,
        params,
    )

    # Alertas recentes disparados
    alert_rows = fetch_all(
        """
        SELECT
            fa.id_alerta, fa.disparado_em, fa.sku, dl.nome AS nome_loja,
            fa.preco_pix, fa.mediana_referencia, fa.z_score_robusto,
            fa.percentil_hist, fa.score_oportunidade, fa.classificacao,
            fa.motivo_gatilho, fa.enviado_sucesso, fa.url_oferta
        FROM fato_alertas fa
        INNER JOIN dim_lojas dl ON dl.id_loja = fa.id_loja
        ORDER BY fa.disparado_em DESC
        LIMIT 20
        """
    )

    counts_by_origin = fetch_all(
        "SELECT origem_dados, COUNT(*) AS total FROM fato_precos GROUP BY origem_dados"
    )

    cfg = load_settings()
    raw_payload = {
        "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "google_sheet_url": f"https://docs.google.com/spreadsheets/d/{cfg.google_spreadsheet_id}/edit",
        "telegram_configurado": bool(cfg.telegram_bot_token and cfg.telegram_chat_id),
        "contagem_origens": {r["origem_dados"]: int(r["total"]) for r in counts_by_origin},
        "skus": reports,
        "serie_diaria": series_rows,
        "ranking_lojas": ranking_rows,
        "auditoria_metade_dobro": audit_rows,
        "alertas_recentes": alert_rows,
    }
    # Normaliza Decimals/datetimes para tipos JSON nativos
    return json.loads(json.dumps(raw_payload, default=_json_default, ensure_ascii=False))


def export_portfolio_snapshot(output_path: Path = SNAPSHOT_PATH) -> Path:
    """Salva snapshot JSON estático sanitizado (sem IDs privados) para o repositório público."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_analytics_payload(origem_filter=None)
    payload["google_sheet_url"] = "#"
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


app = FastAPI(
    title="Radar de Preços & Buy-Timing Analytics API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class NewSourceRequest(BaseModel):
    sku: str
    loja_slug: str
    url: str


@app.get("/api/analytics")
def get_analytics(origem: str | None = None) -> dict[str, Any]:
    return build_analytics_payload(origem_filter=origem)


@app.post("/api/collect")
def trigger_collection() -> dict[str, Any]:
    inserted = run_live_collection()
    reports = evaluate_all_skus()
    alerts = process_alerts(reports)
    export_portfolio_snapshot()
    return {
        "ok": True,
        "novas_coletas_live": inserted,
        "alertas_disparados": alerts,
    }


@app.post("/api/sources")
def add_monitored_source(req: NewSourceRequest) -> dict[str, Any]:
    lojas = fetch_all("SELECT id_loja FROM dim_lojas WHERE slug = %s", (req.loja_slug,))
    if not lojas:
        raise HTTPException(status_code=400, detail="Loja não encontrada")
    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO fontes_monitoradas (sku, id_loja, tipo_coleta, url_ou_termo, ativo)
            VALUES (%s, %s, 'URL_DIRETA_JSONLD', %s, 1)
            """,
            (req.sku, int(lojas[0]["id_loja"]), req.url.strip()),
        )
    return {"ok": True, "mensagem": "Fonte cadastrada com sucesso!"}


if DASHBOARD_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
