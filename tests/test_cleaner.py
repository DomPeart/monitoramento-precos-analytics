from src.etl.cleaner import infer_apple_watch_se_sku, should_persist_cdc, validate_offer_for_sku


def test_infer_se2_40mm_gps():
    title = "Apple Watch SE (2ª geração) GPS Caixa Meia-noite de Alumínio 40mm"
    assert infer_apple_watch_se_sku(title) == "AW-SE2-40-GPS"


def test_infer_se3_44mm_gps():
    title = "Novo Apple Watch SE 3 GPS 44mm Tela Always-On Chip S10 Estelar (2025)"
    assert infer_apple_watch_se_sku(title) == "AW-SE3-44-GPS"


def test_rejects_accessories_and_refurbished():
    assert infer_apple_watch_se_sku("Pulseira Esportiva para Apple Watch SE 40mm") is None
    assert infer_apple_watch_se_sku("Apple Watch SE 2 40mm GPS Vitrine Seminovo") is None
    assert infer_apple_watch_se_sku("Case Capa Protetora Apple Watch SE 44mm") is None


def test_rejects_cellular_variant():
    title = "Apple Watch SE (2ª geração) GPS + Cellular 40mm Alumínio"
    assert infer_apple_watch_se_sku(title) is None


def test_validate_offer_price_floor_and_sku_match():
    ok = validate_offer_for_sku(
        title="Apple Watch SE 2ª geração GPS 40mm Meia-noite",
        preco_pix=1849.00,
        target_sku="AW-SE2-40-GPS",
        preco_min_valido=1300.00,
        preco_max_valido=3200.00,
    )
    assert ok.is_valid is True

    too_cheap = validate_offer_for_sku(
        title="Apple Watch SE 2ª geração GPS 40mm",
        preco_pix=299.00,
        target_sku="AW-SE2-40-GPS",
        preco_min_valido=1300.00,
        preco_max_valido=3200.00,
    )
    assert too_cheap.is_valid is False

    wrong_size = validate_offer_for_sku(
        title="Apple Watch SE 2ª geração GPS 44mm",
        preco_pix=1999.00,
        target_sku="AW-SE2-40-GPS",
        preco_min_valido=1300.00,
        preco_max_valido=3200.00,
    )
    assert wrong_size.is_valid is False


def test_cdc_persists_price_change_and_6h_heartbeat():
    # Primeira leitura -> persiste
    assert should_persist_cdc(1999.0, None, None) == (True, "LIVE_INICIAL")
    # Preço idêntico há 15 minutos -> ignora (não polui o banco!)
    assert should_persist_cdc(1999.0, 1999.0, 0.25) == (False, "ESTAVEL_IGNORADO")
    # Preço caiu R$ 150 em 15 minutos -> persiste na hora!
    assert should_persist_cdc(1849.0, 1999.0, 0.25) == (True, "LIVE_QUEDA")
    # Preço idêntico, mas passaram-se 6.1 horas -> persiste Heartbeat!
    assert should_persist_cdc(1999.0, 1999.0, 6.1) == (True, "LIVE_HEARTBEAT_6H")
