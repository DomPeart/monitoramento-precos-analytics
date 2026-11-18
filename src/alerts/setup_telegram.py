"""
Utilitário interativo para vincular seu Bot do Telegram em 30 segundos.
Uso:
  1. No celular, instale o Telegram e procure por @BotFather
  2. Envie /newbot, escolha um nome e copie o Token gerado para TELEGRAM_BOT_TOKEN no arquivo .env
  3. Abra a conversa com seu novo bot, toque em "Começar" (/start) e envie um "Oi"
  4. Rode: .\.venv\Scripts\python.exe -m src.alerts.setup_telegram
"""
from __future__ import annotations

import re
import sys
import requests

from src.config import BASE_DIR, settings
from src.alerts.telegram_bot import send_telegram_text


def main() -> None:
    token = settings.telegram_bot_token
    if not token:
        print("=" * 68)
        print("PASSO A PASSO RÁPIDO PARA ATIVAR O BOT DO TELEGRAM:")
        print("1. Instale o Telegram no celular e busque por: @BotFather (selo azul)")
        print("2. Envie o comando: /newbot")
        print("3. Dê um nome (ex: Radar Apple Watch) e um usuário terminado em 'bot'")
        print("4. Cole o token recebido no arquivo .env em TELEGRAM_BOT_TOKEN=...")
        print("5. Mande um 'Oi' para o seu novo bot e rode este comando novamente!")
        print("=" * 68)
        sys.exit(0)

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = requests.get(url, timeout=12)
    data = resp.json()
    if not data.get("ok"):
        print(f"Erro ao validar token no Telegram: {data}")
        sys.exit(1)

    results = data.get("result", [])
    if not results:
        print("Token válido! Agora abra seu bot no Telegram, toque em 'Começar' (/start),")
        print("envie qualquer mensagem (ex: 'Oi') e execute este script mais uma vez.")
        sys.exit(0)

    last_msg = results[-1].get("message") or results[-1].get("edited_message") or {}
    chat = last_msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    first_name = chat.get("first_name", "Usuário")

    if not chat_id:
        print("Não foi possível extrair o chat_id. Envie uma nova mensagem 'Oi' para o bot.")
        sys.exit(1)

    env_path = BASE_DIR / ".env"
    env_text = env_path.read_text(encoding="utf-8")
    if "TELEGRAM_CHAT_ID=" in env_text:
        env_text = re.sub(r"^TELEGRAM_CHAT_ID=.*$", f"TELEGRAM_CHAT_ID={chat_id}", env_text, flags=re.M)
    else:
        env_text += f"\nTELEGRAM_CHAT_ID={chat_id}\n"
    env_path.write_text(env_text, encoding="utf-8")

    print(f"Chat ID {chat_id} ({first_name}) salvo com sucesso no .env!")
    test_url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(
        test_url,
        json={
            "chat_id": chat_id,
            "text": (
                "✅ <b>Radar de Preços Conectado!</b>\n\n"
                "Seu monitoramento do <b>Apple Watch SE (2ª Geração 40mm)</b> já está pronto "
                "para avisar sempre que surgir uma oferta estatisticamente boa."
            ),
            "parse_mode": "HTML",
        },
        timeout=12,
    )
    print("Mensagem de teste enviada para o seu celular!")


if __name__ == "__main__":
    main()
