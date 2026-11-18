"""Módulo de limpeza, higienização e classificação de anúncios da linha Apple Watch SE."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def normalize_text(text: str) -> str:
    """Remove acentos e normaliza espaços em caixa baixa para comparação robusta."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_str = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_str.lower()).strip()


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    reason: str
    inferred_sku: str | None = None


DEFAULT_BLOCKED_TERMS = (
    "pulseira",
    "case",
    "capa",
    "capinha",
    "pelicula",
    "protetor",
    "carregador",
    "cabo",
    "suporte",
    "bumper",
    "usado",
    "seminovo",
    "vitrine",
    "recondicionado",
    "refurbished",
    "swap",
    "defeito",
    "sucata",
    "caixa vazia",
    "apenas caixa",
    "series 3",
    "series 6",
    "series 7",
    "series 8",
    "series 9",
    "series 10",
    "ultra",
)


def infer_apple_watch_se_sku(title: str) -> str | None:
    """
    Infere o SKU exato do Apple Watch SE (GPS) a partir do título do anúncio,
    separando Geração (SE 2 vs SE 3), Tamanho (40mm vs 44mm) e descartando Cellular.
    """
    norm = normalize_text(title)
    if "watch" not in norm or not re.search(r"\bse\b", norm):
        return None

    for term in DEFAULT_BLOCKED_TERMS:
        if term in norm:
            return None

    # Descarta versões GPS + Cellular para não distorcer a curva do modelo GPS
    if "cellular" in norm or "lte" in norm or "+ cel" in norm:
        return None

    # Identifica tamanho da caixa
    has_40 = bool(re.search(r"\b40\s*mm\b|\b40mm\b", norm))
    has_44 = bool(re.search(r"\b44\s*mm\b|\b44mm\b", norm))
    if has_40 and has_44:
        return None
    if not has_40 and not has_44:
        return None
    size = "40" if has_40 else "44"

    # Identifica geração (3ª Geração = SE 3 / 2025 / S10 vs 2ª Geração = SE 2 / 2022-2024 / S8)
    is_gen3 = bool(
        re.search(
            r"\b(se\s*3|3a\s*geracao|3\s*geracao|geracao\s*3|chip\s*s10|2025)\b",
            norm,
        )
    )
    is_gen1 = bool(re.search(r"\b(1a\s*geracao|geracao\s*1|2020|chip\s*s5)\b", norm))
    if is_gen1:
        return None

    gen = "SE3" if is_gen3 else "SE2"
    return f"AW-{gen}-{size}-GPS"


def validate_offer_for_sku(
    title: str,
    preco_pix: float,
    target_sku: str,
    preco_min_valido: float,
    preco_max_valido: float,
    extra_blocked_csv: str = "",
) -> ValidationResult:
    """Valida se um anúncio capturado pertence legitimamente ao SKU monitorado."""
    if preco_pix <= 0:
        return ValidationResult(False, "Preço inválido ou zerado")

    if preco_pix < preco_min_valido:
        return ValidationResult(
            False,
            f"Preço PIX (R$ {preco_pix:.2f}) abaixo do piso anti-ruído (R$ {preco_min_valido:.2f})",
        )

    if preco_pix > preco_max_valido:
        return ValidationResult(
            False,
            f"Preço PIX (R$ {preco_pix:.2f}) acima do teto válido (R$ {preco_max_valido:.2f})",
        )

    norm = normalize_text(title)
    custom_blocked = [normalize_text(x) for x in extra_blocked_csv.split(",") if x.strip()]
    for blocked in (*DEFAULT_BLOCKED_TERMS, *custom_blocked):
        if blocked and blocked in norm:
            return ValidationResult(False, f"Termo bloqueado detectado: '{blocked}'")

    inferred = infer_apple_watch_se_sku(title)
    if inferred != target_sku:
        return ValidationResult(
            False,
            f"Variante divergente (esperado {target_sku}, inferido {inferred})",
            inferred_sku=inferred,
        )

    return ValidationResult(True, "Anúncio válido", inferred_sku=inferred)


def should_persist_cdc(
    new_price_pix: float,
    last_price_pix: float | None,
    hours_since_last: float | None,
    min_delta_rs: float = 0.50,
    heartbeat_hours: float = 6.0,
) -> tuple[bool, str]:
    """
    Change Data Capture (CDC) + Heartbeat:
    Permite checar preços a cada 15 minutos sem poluir o banco com linhas idênticas.
    Persiste imediatamente se o preço mudar (>= R$ 0,50) ou após `heartbeat_hours` horas.
    """
    if last_price_pix is None or hours_since_last is None:
        return True, "LIVE_INICIAL"

    if abs(new_price_pix - last_price_pix) >= min_delta_rs:
        return True, ("LIVE_QUEDA" if new_price_pix < last_price_pix else "LIVE_SUBIDA")

    if hours_since_last >= heartbeat_hours:
        return True, "LIVE_HEARTBEAT_6H"

    return False, "ESTAVEL_IGNORADO"
