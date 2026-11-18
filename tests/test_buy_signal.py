from src.analytics.buy_signal import compute_robust_zscore, score_offer


def test_robust_zscore_ignores_extreme_spike():
    # Série normal em torno de R$ 2.150 com 2 picos de "metade do dobro" a R$ 3.100
    history = [2120, 2150, 2140, 2160, 2150, 2130, 2170, 3100, 3100]
    med, mad, z_rob = compute_robust_zscore(1829.0, history)
    assert med == 2150.0
    assert mad == 20.0
    assert z_rob < -5.0  # Queda genuína fortemente detectada apesar dos outliers superiores


def test_strong_buy_signal_when_below_target_and_median():
    history = [2100 + (i % 5) * 20 for i in range(30)]  # ~2100..2180
    res = score_offer(
        preco_atual=1829.0,
        preco_alvo=1899.0,
        preco_parcelado=2066.0,
        series_historica=history,
        store_recent_peak_10d=2140.0,
        store_30d_median=2140.0,
    )
    assert res["score"] >= 85
    assert res["classificacao"] == "COMPRA FORTE"
    assert res["fake_discount"] is False


def test_penalizes_metade_do_dobro():
    history = [2120, 2140, 2150, 2130, 2160, 2150, 2140, 2490, 2490]
    res = score_offer(
        preco_atual=2165.0,  # Caiu de 2490, mas ainda acima da mediana 2140!
        preco_alvo=1899.0,
        preco_parcelado=2399.0,
        series_historica=history,
        store_recent_peak_10d=2490.0,
        store_30d_median=2145.0,
    )
    assert res["fake_discount"] is True
    assert res["score"] < 40
