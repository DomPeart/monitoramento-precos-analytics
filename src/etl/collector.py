"""
Coletor de preços estruturado (JSON-LD Schema.org, Meta Tags e páginas públicas).
Respeita estritamente limites de acesso (nunca tenta burlar CAPTCHAs ou bloqueios anti-bot).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
import requests
from bs4 import BeautifulSoup

from src.database import db_cursor, fetch_all, fetch_one
from src.etl.cleaner import infer_apple_watch_se_sku, should_persist_cdc, validate_offer_for_sku

logger = logging.getLogger(__name__)

STANDARD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
}

ANTIBOT_MARKERS = (
    "verify you are human",
    "captcha",
    "cf-chl-bypass",
    "turnstile",
    "access denied",
    "robot check",
)


@dataclass
class RawOffer:
    sku: str
    id_loja: int
    loja_slug: str
    titulo_anuncio: str
    vendedor: str | None
    preco_pix: float
    preco_parcelado: float
    max_parcelas: int
    disponivel: bool
    url_oferta: str


def _is_blocked_by_gate(status_code: int, html: str) -> bool:
    if status_code in (401, 403, 429, 503):
        return True
    sample = (html or "")[:5000].lower()
    return any(marker in sample for marker in ANTIBOT_MARKERS)


def _parse_brl_number(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    raw = str(val).strip()
    if not raw:
        return None
    cleaned = re.sub(r"[^\d,\.]", "", raw)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_all_jsonld_products(html: str, default_url: str) -> list[dict[str, Any]]:
    """Extrai todos os nós schema.org/Product presentes em blocos JSON-LD (página única ou vitrine)."""
    soup = BeautifulSoup(html, "html.parser")
    found: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw_json = script.string or script.get_text()
        if not raw_json:
            continue
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            continue

        queue = payload if isinstance(payload, list) else [payload]
        idx = 0
        while idx < len(queue):
            node = queue[idx]
            idx += 1
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            if isinstance(graph, list):
                queue.extend(x for x in graph if isinstance(x, dict))
            item_list = node.get("itemListElement")
            if isinstance(item_list, list):
                for el in item_list:
                    if isinstance(el, dict):
                        queue.append(el.get("item") if isinstance(el.get("item"), dict) else el)

            node_type = str(node.get("@type", "")).lower()
            if "product" in node_type:
                name = str(node.get("name", "")).strip()
                offers = node.get("offers")
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if isinstance(offers, dict):
                    price = _parse_brl_number(offers.get("lowPrice") or offers.get("price"))
                    high_price = _parse_brl_number(offers.get("highPrice")) or price
                    avail = str(offers.get("availability", "InStock")).lower()
                    in_stock = "outofstock" not in avail and "soldout" not in avail
                    offer_url = str(offers.get("url") or node.get("url") or default_url)
                    if name and price:
                        found.append(
                            {
                                "title": name,
                                "price_pix": price,
                                "price_installment": (
                                    high_price if high_price and high_price >= price else round(price / 0.90, 2)
                                ),
                                "available": in_stock,
                                "url": offer_url,
                            }
                        )
    return found


def extract_jsonld_product(html: str, url: str) -> dict[str, Any] | None:
    """Extrai o primeiro produto estruturado schema.org/Product de uma URL direta."""
    items = extract_all_jsonld_products(html, url)
    return items[0] if items else None


def collect_from_direct_url(source: dict[str, Any], product: dict[str, Any]) -> list[RawOffer]:
    """Coleta oferta a partir de uma URL direta de produto via JSON-LD."""
    url = source["url_ou_termo"]
    try:
        resp = requests.get(url, headers=STANDARD_HEADERS, timeout=15)
    except requests.RequestException as exc:
        logger.warning("Falha de rede em %s: %s", url, exc)
        _update_source_status(source["id_fonte"], f"ERRO_REDE: {type(exc).__name__}")
        return []

    if _is_blocked_by_gate(resp.status_code, resp.text):
        logger.info("Acesso restrito/anti-bot em %s (HTTP %s). Pulando sem forçar.", url, resp.status_code)
        _update_source_status(source["id_fonte"], f"BLOQUEADO_HTTP_{resp.status_code}")
        return []

    parsed = extract_jsonld_product(resp.text, url)
    if not parsed:
        _update_source_status(source["id_fonte"], "SEM_JSONLD")
        return []

    check = validate_offer_for_sku(
        title=parsed["title"],
        preco_pix=parsed["price_pix"],
        target_sku=product["sku"],
        preco_min_valido=float(product["preco_min_valido"]),
        preco_max_valido=float(product["preco_max_valido"]),
        extra_blocked_csv=product.get("termos_bloqueados", ""),
    )
    if not check.is_valid:
        logger.info("Oferta descartada (%s): %s", parsed["title"], check.reason)
        _update_source_status(source["id_fonte"], f"DESCARTADO: {check.reason[:40]}")
        return []

    _update_source_status(source["id_fonte"], "OK")
    return [
        RawOffer(
            sku=product["sku"],
            id_loja=int(source["id_loja"]),
            loja_slug=source["loja_slug"],
            titulo_anuncio=parsed["title"][:250],
            vendedor=source["nome_loja"],
            preco_pix=round(parsed["price_pix"], 2),
            preco_parcelado=round(parsed["price_installment"], 2),
            max_parcelas=10,
            disponivel=bool(parsed["available"]),
            url_oferta=parsed["url"][:690],
        )
    ]


def collect_mercadolivre_search(source: dict[str, Any], product: dict[str, Any]) -> list[RawOffer]:
    """
    Busca anúncios novos no Mercado Livre Brasil a partir da listagem pública HTML/JSON-LD,
    filtrando rigorosamente por SKU (geração, tamanho e condição de novo).
    """
    query = source["url_ou_termo"]
    slug_query = re.sub(r"\s+", "-", query.strip().lower())
    search_url = f"https://lista.mercadolivre.com.br/{slug_query}_Novo"
    try:
        resp = requests.get(search_url, headers=STANDARD_HEADERS, timeout=15)
    except requests.RequestException as exc:
        logger.warning("Erro ao consultar Mercado Livre (%s): %s", query, exc)
        _update_source_status(source["id_fonte"], f"ERRO_REDE: {type(exc).__name__}")
        return []

    if _is_blocked_by_gate(resp.status_code, resp.text):
        logger.info("Mercado Livre exigiu verificação/bloqueio (HTTP %s). Pulando de forma segura.", resp.status_code)
        _update_source_status(source["id_fonte"], f"RESTRITO_HTTP_{resp.status_code}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    offers: list[RawOffer] = []

    candidates_raw: list[tuple[str, float, str]] = []
    for jitem in extract_all_jsonld_products(resp.text, search_url):
        candidates_raw.append((jitem["title"], float(jitem["price_pix"]), jitem["url"]))

    cards = soup.select("li.ui-search-layout__item, div.poly-card, div.ui-search-result")
    for card in cards[:20]:
        title_el = card.select_one("h2, h3, a.poly-component__title")
        link_el = card.select_one("a[href]")
        price_el = card.select_one("span.andes-money-amount__fraction")
        if not (title_el and link_el and price_el):
            continue
        title_txt = title_el.get_text(" ", strip=True)
        pval = _parse_brl_number(price_el.get_text(strip=True))
        href_txt = link_el.get("href", "").split("#")[0]
        if pval and href_txt:
            candidates_raw.append((title_txt, pval, href_txt))

    for title, price_val, href in candidates_raw:
        inferred = infer_apple_watch_se_sku(title)
        if inferred != product["sku"]:
            continue

        check = validate_offer_for_sku(
            title=title,
            preco_pix=price_val,
            target_sku=product["sku"],
            preco_min_valido=float(product["preco_min_valido"]),
            preco_max_valido=float(product["preco_max_valido"]),
            extra_blocked_csv=product.get("termos_bloqueados", ""),
        )
        if not check.is_valid:
            continue

        offers.append(
            RawOffer(
                sku=product["sku"],
                id_loja=int(source["id_loja"]),
                loja_slug=source["loja_slug"],
                titulo_anuncio=title[:250],
                vendedor="Mercado Livre (Novo)",
                preco_pix=round(price_val, 2),
                preco_parcelado=round(price_val * 1.08, 2),
                max_parcelas=10,
                disponivel=True,
                url_oferta=href[:690],
            )
        )

    if not offers:
        _update_source_status(source["id_fonte"], "SEM_ITENS_VALIDOS")
        return []

    # Seleciona o menor preço válido encontrado na vitrine de novos
    best = min(offers, key=lambda o: o.preco_pix)
    _update_source_status(source["id_fonte"], f"OK ({len(offers)} válidos)")
    return [best]


def _update_source_status(id_fonte: int, status: str) -> None:
    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE fontes_monitoradas
               SET ultima_coleta = NOW(),
                   ultimo_status = %s
             WHERE id_fonte = %s
            """,
            (status[:60], id_fonte),
        )


def ensure_default_sources() -> None:
    """Garante que fontes padrão de busca estejam cadastradas para os 4 SKUs."""
    existing = fetch_all("SELECT COUNT(*) AS qtd FROM fontes_monitoradas")
    if existing and int(existing[0]["qtd"]) > 0:
        return

    ml_rows = fetch_all("SELECT id_loja FROM dim_lojas WHERE slug = 'mercadolivre'")
    if not ml_rows:
        return
    ml_id = int(ml_rows[0]["id_loja"])

    defaults = [
        ("AW-SE2-40-GPS", ml_id, "MERCADOLIVRE_BUSCA", "apple watch se 2 geracao 40mm gps"),
        ("AW-SE2-44-GPS", ml_id, "MERCADOLIVRE_BUSCA", "apple watch se 2 geracao 44mm gps"),
        ("AW-SE3-40-GPS", ml_id, "MERCADOLIVRE_BUSCA", "apple watch se 3 geracao 40mm gps"),
        ("AW-SE3-44-GPS", ml_id, "MERCADOLIVRE_BUSCA", "apple watch se 3 geracao 44mm gps"),
    ]
    with db_cursor(commit=True) as cur:
        cur.executemany(
            """
            INSERT INTO fontes_monitoradas (sku, id_loja, tipo_coleta, url_ou_termo, ativo)
            VALUES (%s, %s, %s, %s, 1)
            """,
            defaults,
        )


def run_live_collection() -> int:
    """Executa coleta ao vivo em todas as fontes ativas e grava em fato_precos."""
    ensure_default_sources()
    products_by_sku = {
        row["sku"]: row
        for row in fetch_all("SELECT * FROM dim_produtos WHERE ativo = 1")
    }
    sources = fetch_all(
        """
        SELECT fm.*, dl.slug AS loja_slug, dl.nome AS nome_loja
          FROM fontes_monitoradas fm
          INNER JOIN dim_lojas dl ON dl.id_loja = fm.id_loja
         WHERE fm.ativo = 1 AND dl.ativa = 1
        """
    )

    collected: list[RawOffer] = []
    for src in sources:
        prod = products_by_sku.get(src["sku"])
        if not prod:
            continue
        if src["tipo_coleta"] == "MERCADOLIVRE_BUSCA":
            collected.extend(collect_mercadolivre_search(src, prod))
        elif src["tipo_coleta"] == "URL_DIRETA_JSONLD":
            collected.extend(collect_from_direct_url(src, prod))

    if not collected:
        return 0

    now_dt = datetime.now()
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    inserted_count = 0

    with db_cursor(commit=True) as cur:
        for item in collected:
            last_row = fetch_one(
                """
                SELECT preco_pix, coletado_em
                  FROM fato_precos
                 WHERE sku = %s AND id_loja = %s
                 ORDER BY coletado_em DESC, id_coleta DESC
                 LIMIT 1
                """,
                (item.sku, item.id_loja),
            )
            last_price = float(last_row["preco_pix"]) if last_row else None
            hours_elapsed = (
                (now_dt - last_row["coletado_em"]).total_seconds() / 3600.0
                if last_row and isinstance(last_row["coletado_em"], datetime)
                else None
            )
            persist, reason = should_persist_cdc(item.preco_pix, last_price, hours_elapsed)
            if not persist:
                logger.info("CDC ignorou leitura repetida (%s loja %d): R$ %.2f (%s)", item.sku, item.id_loja, item.preco_pix, reason)
                continue

            cur.execute(
                """
                INSERT INTO fato_precos (
                    coletado_em, sku, id_loja, titulo_anuncio, vendedor,
                    preco_pix, preco_parcelado, max_parcelas, disponivel,
                    url_oferta, origem_dados
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'LIVE')
                """,
                (
                    now_str,
                    item.sku,
                    item.id_loja,
                    item.titulo_anuncio,
                    item.vendedor,
                    item.preco_pix,
                    item.preco_parcelado,
                    item.max_parcelas,
                    1 if item.disponivel else 0,
                    item.url_oferta,
                ),
            )
            inserted_count += 1
    return inserted_count
