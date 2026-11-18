"""
Sincronizador Python -> Google BigQuery Sandbox / Free Tier.

IMPORTANTE (Blindagem de Custo R$ 0,00):
Utiliza EXCLUSIVAMENTE Batch Load Jobs (`client.load_table_from_json`), que são 100% gratuitos
e funcionam no BigQuery Sandbox sem cartão de crédito.
NUNCA utiliza `insert_rows_json` (Streaming Insert), pois streaming não entra no Free Tier.
"""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from src.config import load_settings
from src.database import fetch_all

logger = logging.getLogger(__name__)


def sync_mysql_to_bigquery() -> dict[str, Any]:
    """Exporta dim_produtos, fato_precos e fato_alertas do MySQL local para o BigQuery via Batch Load."""
    cfg = load_settings()
    if not cfg.gcp_project_id:
        raise ValueError("Defina GCP_PROJECT_ID no arquivo .env antes de sincronizar com o BigQuery.")

    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise RuntimeError(
            "Instale o cliente oficial com: .\\.venv\\Scripts\\pip.exe install google-cloud-bigquery"
        ) from exc

    client = bigquery.Client(project=cfg.gcp_project_id)
    dataset_ref = f"{cfg.gcp_project_id}.{cfg.bigquery_dataset}"

    # Cria dataset caso ainda não exista (compatível com BigQuery Sandbox)
    ds = bigquery.Dataset(dataset_ref)
    ds.location = "southamerica-east1"
    client.create_dataset(ds, exists_ok=True)

    # 1. Carga da Dimensão de Produtos (WRITE_TRUNCATE via Batch Load gratuito)
    produtos = fetch_all("SELECT * FROM dim_produtos")
    prod_payload = [
        {
            "sku": r["sku"],
            "nome": r["nome"],
            "geracao": r["geracao"],
            "tamanho_mm": int(r["tamanho_mm"]),
            "conectividade": r["conectividade"],
            "favorito": bool(r["favorito"]),
            "ativo": bool(r["ativo"]),
            "preco_alvo_pix": str(r["preco_alvo_pix"]),
            "preco_min_valido": str(r["preco_min_valido"]),
            "preco_max_valido": str(r["preco_max_valido"]),
            "desconto_mediana_gatilho": float(r["desconto_mediana_gatilho"]),
            "termos_obrigatorios": r["termos_obrigatorios"],
            "termos_bloqueados": r["termos_bloqueados"],
            "criado_em": r["criado_em"].isoformat() if isinstance(r["criado_em"], datetime) else str(r["criado_em"]),
        }
        for r in produtos
    ]

    job_prod = client.load_table_from_json(
        prod_payload,
        f"{dataset_ref}.dim_produtos",
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            autodetect=True,
        ),
    )
    job_prod.result()

    # 2. Carga da Fato de Preços (Batch Load gratuito)
    precos = fetch_all(
        """
        SELECT fp.*, dl.slug AS loja_slug, dl.nome AS nome_loja
          FROM fato_precos fp
          INNER JOIN dim_lojas dl ON dl.id_loja = fp.id_loja
         ORDER BY fp.id_coleta ASC
        """
    )
    precos_payload = [
        {
            "coletado_em": r["coletado_em"].isoformat() if isinstance(r["coletado_em"], datetime) else str(r["coletado_em"]),
            "sku": r["sku"],
            "loja_slug": r["loja_slug"],
            "nome_loja": r["nome_loja"],
            "titulo_anuncio": r["titulo_anuncio"],
            "preco_pix": float(r["preco_pix"]),
            "preco_parcelado": float(r["preco_parcelado"]),
            "max_parcelas": int(r["max_parcelas"]),
            "spread_pix_pct": float(r["spread_pix_pct"]),
            "disponivel": bool(r["disponivel"]),
            "url_oferta": r["url_oferta"],
            "origem_dados": r["origem_dados"],
        }
        for r in precos
    ]

    job_precos = client.load_table_from_json(
        precos_payload,
        f"{dataset_ref}.fato_precos",
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            autodetect=True,
        ),
    )
    job_precos.result()

    logger.info(
        "Sincronização BigQuery concluída (Batch Load R$ 0): %d produtos e %d leituras.",
        len(prod_payload),
        len(precos_payload),
    )
    return {"produtos": len(prod_payload), "leituras_precos": len(precos_payload)}
