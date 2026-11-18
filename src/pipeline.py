"""
Orquestrador CLI do Pipeline ETL + Estatística + Alertas + Exportação Google Sheets / Portfólio.

Exemplos de uso:
  .\.venv\Scripts\python.exe -m src.pipeline --completo
  .\.venv\Scripts\python.exe -m src.pipeline --coletar
  .\.venv\Scripts\python.exe -m src.pipeline --limpar-calibracao
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from src.alerts.telegram_bot import process_alerts
from src.analytics.buy_signal import evaluate_all_skus
from src.api.server import export_portfolio_snapshot
from src.config import BASE_DIR
from src.database import fetch_all
from src.etl.collector import ensure_default_sources, run_live_collection
from src.etl.seed_calibration import clear_calibration_history, seed_calibration_history

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")


def generate_gsheets_sync_file(out_file: Path) -> Path:
    """
    Gera arquivo JSON de batch compatível com `gsheets mutate batch` para manter
    a planilha Google Sheets sincronizada com o banco MySQL.
    """
    produtos = fetch_all(
        """
        SELECT sku, nome, IF(ativo=1,'SIM','NAO') AS ativo_str,
               preco_alvo_pix, preco_min_valido, desconto_mediana_gatilho,
               termos_obrigatorios, termos_bloqueados
          FROM dim_produtos
         ORDER BY favorito DESC, sku ASC
        """
    )
    fontes = fetch_all(
        """
        SELECT fm.id_fonte, fm.sku, dl.nome AS loja,
               IF(fm.ativo=1,'SIM','NAO') AS ativo_str,
               fm.url_ou_termo, COALESCE(fm.ultimo_status, 'Aguardando') AS obs
          FROM fontes_monitoradas fm
          INNER JOIN dim_lojas dl ON dl.id_loja = fm.id_loja
         ORDER BY fm.id_fonte ASC
        """
    )
    historico = fetch_all(
        """
        SELECT DATE_FORMAT(fp.coletado_em, '%%Y-%%m-%%d %%H:%%i:%%s') AS dh,
               fp.sku, dl.nome AS loja, fp.titulo_anuncio,
               fp.preco_pix, fp.preco_parcelado, fp.max_parcelas,
               IF(fp.disponivel=1,'SIM','NAO') AS disp,
               fp.origem_dados, fp.url_oferta
          FROM fato_precos fp
          INNER JOIN dim_lojas dl ON dl.id_loja = fp.id_loja
         ORDER BY fp.coletado_em DESC
         LIMIT 250
        """
    )
    alertas = fetch_all(
        """
        SELECT DATE_FORMAT(fa.disparado_em, '%%Y-%%m-%%d %%H:%%i:%%s') AS dh,
               fa.sku, dl.nome AS loja, fa.preco_pix,
               fa.minimo_historico, fa.mediana_referencia,
               CONCAT('[Score ', fa.score_oportunidade, '] ', fa.motivo_gatilho) AS motivo,
               fa.canal, fa.url_oferta
          FROM fato_alertas fa
          INNER JOIN dim_lojas dl ON dl.id_loja = fa.id_loja
         ORDER BY fa.disparado_em DESC
         LIMIT 50
        """
    )

    ops: list[dict] = []
    if produtos:
        prod_rows = [
            [
                r["sku"],
                ("⭐ " + r["nome"]) if idx == 0 else r["nome"],
                r["ativo_str"],
                float(r["preco_alvo_pix"]),
                float(r["preco_min_valido"]),
                float(r["desconto_mediana_gatilho"]),
                r["termos_obrigatorios"],
                r["termos_bloqueados"],
            ]
            for idx, r in enumerate(produtos)
        ]
        ops.append({"op": "write", "range": f"Produtos!A2:H{1 + len(prod_rows)}", "data": prod_rows})

    if fontes:
        fontes_rows = [
            [int(r["id_fonte"]), r["sku"], r["loja"], r["ativo_str"], r["url_ou_termo"], r["obs"]]
            for r in fontes
        ]
        ops.append({"op": "write", "range": f"Fontes!A2:F{1 + len(fontes_rows)}", "data": fontes_rows})

    if historico:
        # Ordem cronológica ascendente para que a última linha seja a leitura mais recente na planilha
        hist_asc = list(reversed(historico))
        hist_rows = [
            [
                r["dh"], r["sku"], r["loja"], r["titulo_anuncio"],
                float(r["preco_pix"]), float(r["preco_parcelado"]), int(r["max_parcelas"]),
                r["disp"], r["origem_dados"], r["url_oferta"],
            ]
            for r in hist_asc
        ]
        ops.append({"op": "write", "range": f"Historico!A2:J{1 + len(hist_rows)}", "data": hist_rows})

    if alertas:
        alert_rows = [
            [
                r["dh"], r["sku"], r["loja"], float(r["preco_pix"]),
                float(r["minimo_historico"]), float(r["mediana_referencia"]),
                r["motivo"], r["canal"], r["url_oferta"],
            ]
            for r in alertas
        ]
        ops.append({"op": "write", "range": f"Alertas!A2:I{1 + len(alert_rows)}", "data": alert_rows})

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(ops, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline Radar de Preços — Apple Watch SE")
    parser.add_argument("--completo", action="store_true", help="Executa seed + coleta ao vivo + score + alertas + snapshot")
    parser.add_argument("--seed", action="store_true", help="Popula série histórica de calibração de 35 dias")
    parser.add_argument("--coletar", action="store_true", help="Executa coleta ao vivo nas fontes ativas")
    parser.add_argument("--limpar-calibracao", action="store_true", help="Remove apenas a série de calibração")
    parser.add_argument("--gerar-sheets-json", type=str, default="", help="Caminho para salvar JSON de sync com Google Sheets")
    args = parser.parse_args()

    if args.limpar_calibracao:
        removed = clear_calibration_history()
        logger.info("Removidos %d registros de calibração.", removed)
        export_portfolio_snapshot()
        return

    ensure_default_sources()

    if args.completo or args.seed:
        seeded = seed_calibration_history(days=35)
        if seeded:
            logger.info("Série histórica de calibração inserida no MySQL: %d leituras.", seeded)

    if args.completo or args.coletar:
        live_count = run_live_collection()
        logger.info("Coleta ao vivo concluída: %d novas ofertas válidas salvas.", live_count)

    reports = evaluate_all_skus()
    print("\n" + "=" * 78)
    print("RESUMO DO MOTOR ESTATÍSTICO DE COMPRA (APPLE WATCH SE)")
    print("=" * 78)
    for rep in reports:
        fav_tag = " [FAVORITO ⭐]" if rep.favorito else ""
        print(
            f"• {rep.sku}{fav_tag}: R$ {rep.preco_atual_pix:,.2f} na {rep.melhor_loja} "
            f"| Mediana: R$ {rep.mediana_historica:,.2f} | P{rep.percentil_atual:.0f} "
            f"| Z={rep.z_score_robusto:+.2f} | Score: {rep.score_oportunidade}/100 ({rep.classificacao})"
        )
    print("=" * 78 + "\n")

    triggered = process_alerts(reports)
    if triggered:
        logger.info("Alertas de oferta boa gerados: %s", triggered)

    snap = export_portfolio_snapshot()
    logger.info("Snapshot para Dashboard/Portfólio exportado em: %s", snap)

    if args.gerar_sheets_json:
        out_p = generate_gsheets_sync_file(Path(args.gerar_sheets_json))
        logger.info("Arquivo de batch Google Sheets salvo em: %s", out_p)


if __name__ == "__main__":
    main()
