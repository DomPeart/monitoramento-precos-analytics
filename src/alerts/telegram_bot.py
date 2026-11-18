"""
Disparador de ofertas boas via Telegram Bot API com controle de cooldown no MySQL.
"""
from __future__ import annotations

import logging
import requests

from src.analytics.buy_signal import BuySignalReport
from src.config import load_settings
from src.database import db_cursor, fetch_one

logger = logging.getLogger(__name__)


def format_telegram_message(report: BuySignalReport) -> str:
    """Formata card rico em HTML para envio no Telegram."""
    star = "⭐ <b>[SEU FAVORITO]</b>\n" if report.favorito else ""
    motivos_txt = "\n".join(f"  • {m}" for m in report.motivos)
    economia_med = report.mediana_historica - report.preco_atual_pix

    return (
        f"🔥 <b>ALERTA DE OFERTA BOA — SCORE {report.score_oportunidade}/100</b>\n"
        f"{star}"
        f"⌚ <b>{report.nome_produto}</b>\n\n"
        f"🏪 <b>Loja:</b> {report.melhor_loja}\n"
        f"💸 <b>Preço PIX:</b> R$ {report.preco_atual_pix:,.2f}\n"
        f"💳 <b>Parcelado:</b> R$ {report.preco_parcelado:,.2f} (-{report.spread_pix_pct:.1f}% no PIX)\n"
        f"🎯 <b>Seu Preço-Alvo:</b> R$ {report.preco_alvo_pix:,.2f}\n\n"
        f"📊 <b>Contexto Estatístico ({report.total_amostras} leituras):</b>\n"
        f"  • Mediana histórica: R$ {report.mediana_historica:,.2f} (Economia: R$ {economia_med:,.2f})\n"
        f"  • Mínimo histórico: R$ {report.minimo_historico:,.2f}\n"
        f"  • Posição: <b>Percentil P{report.percentil_atual:.0f}</b> | Z-Score: <b>{report.z_score_robusto:.2f}</b>\n\n"
        f"🧠 <b>Por que vale a pena:</b>\n{motivos_txt}\n\n"
        f"🔗 <a href=\"{report.url_oferta}\">Abrir oferta na {report.melhor_loja}</a>"
    )


def should_dispatch_alert(report: BuySignalReport) -> bool:
    """Verifica se o score atingiu o gatilho e respeita a janela anti-spam."""
    cfg = load_settings()
    if report.score_oportunidade < cfg.alert_min_score:
        return False

    last_alert = fetch_one(
        """
        SELECT preco_pix, disparado_em
          FROM fato_alertas
         WHERE sku = %s
           AND disparado_em >= DATE_SUB(NOW(), INTERVAL %s HOUR)
         ORDER BY disparado_em DESC
         LIMIT 1
        """,
        (report.sku, cfg.alert_cooldown_hours),
    )
    if not last_alert:
        return True

    # Só dispara novamente dentro do cooldown se o preço caiu mais R$ 30+
    prev_price = float(last_alert["preco_pix"])
    return (prev_price - report.preco_atual_pix) >= cfg.alert_extra_drop_rs


def send_telegram_text(message_html: str) -> bool:
    """Envia mensagem via Telegram Bot API caso credenciais estejam configuradas."""
    cfg = load_settings()
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        logger.info("Telegram ainda não configurado no .env (alerta registrado apenas no banco/painel).")
        return False

    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": cfg.telegram_chat_id,
                "text": message_html,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=12,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            return True
        logger.warning("Resposta inesperada do Telegram (%s): %s", resp.status_code, resp.text)
        return False
    except requests.RequestException as exc:
        logger.warning("Falha de rede ao enviar alerta Telegram: %s", exc)
        return False


def is_sleep_window(current_hour: int | None = None) -> bool:
    """Retorna True se estiver na janela de sono (ex.: entre 02h e 06h59 da manhã)."""
    from datetime import datetime

    cfg = load_settings()
    hour = datetime.now().hour if current_hour is None else current_hour
    return cfg.sleep_start_hour <= hour <= cfg.sleep_end_hour


def trigger_telegram_voice_call(report: BuySignalReport) -> bool:
    """
    Faz o celular tocar via chamada de voz gratuita no Telegram (CallMeBot TTS em português).
    Requer envio único prévio de `/start` para `@CallMeBot_txtbot` e definição de TELEGRAM_CALL_USER.
    """
    cfg = load_settings()
    if not cfg.telegram_call_user:
        return False

    speech_text = (
        f"Atenção! Super oferta detectada, score {report.score_oportunidade} de cem. "
        f"{report.nome_produto} por {round(report.preco_atual_pix)} reais no PIX na {report.melhor_loja}. "
        f"O link já está no seu Telegram!"
    )[:235]

    try:
        resp = requests.get(
            "https://api.callmebot.com/start.php",
            params={
                "user": cfg.telegram_call_user,
                "text": speech_text,
                "lang": "pt-BR-Standard-A",
                "rpt": 2,
            },
            timeout=60,
        )
        body = resp.text or ""
        if resp.status_code != 200 or "ERROR:" in body or "not authorized" in body.lower():
            logger.warning("CallMeBot recusou a chamada: %s", body[:200])
            return False
        return True
    except requests.RequestException as exc:
        logger.warning("Falha ao disparar ligação de voz CallMeBot: %s", exc)
        return False


def process_alerts(reports: list[BuySignalReport]) -> list[dict]:
    """Avalia relatórios, registra alertas qualificados em fato_alertas e dispara no Telegram."""
    triggered = []
    madrugada = is_sleep_window()

    for rep in reports:
        if not should_dispatch_alert(rep):
            continue

        msg = format_telegram_message(rep)
        sent_ok = send_telegram_text(msg)
        called_ok = trigger_telegram_voice_call(rep) if madrugada else False
        canal_str = "TELEGRAM+LIGACAO" if called_ok else "TELEGRAM"
        motivo_str = "; ".join(rep.motivos)[:250]

        with db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO fato_alertas (
                    sku, id_loja, preco_pix, mediana_referencia, minimo_historico,
                    z_score_robusto, percentil_hist, score_oportunidade,
                    classificacao, motivo_gatilho, canal, enviado_sucesso, url_oferta
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rep.sku,
                    rep.id_loja_melhor,
                    rep.preco_atual_pix,
                    rep.mediana_historica,
                    rep.minimo_historico,
                    rep.z_score_robusto,
                    rep.percentil_atual,
                    rep.score_oportunidade,
                    rep.classificacao,
                    motivo_str,
                    canal_str,
                    1 if (sent_ok or called_ok) else 0,
                    rep.url_oferta,
                ),
            )
        triggered.append(
            {
                "sku": rep.sku,
                "loja": rep.melhor_loja,
                "preco_pix": rep.preco_atual_pix,
                "score": rep.score_oportunidade,
                "enviado_telegram": sent_ok,
                "ligacao_madrugada": called_ok,
            }
        )
    return triggered
