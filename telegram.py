# Nome do arquivo: telegram.py

import httpx
import os
import json

# Tenta carregar config.json para fallback
CONFIG_FILE = "config.json"
config_data = {}
try:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config_data = json.load(f)
except:
    pass

# Prioridade: Variável de Ambiente > config.json
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or config_data.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or config_data.get("TELEGRAM_CHAT_ID")

# URL da API do Telegram
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

async def send_telegram_message(text: str) -> bool:
    """
    Envia uma mensagem de texto formatada para o grupo do Telegram.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Erro Telegram: Token ou Chat ID não encontrados (Verifique ENV ou config.json).")
        return False

    # Limita a mensagem a 4096 caracteres (limite do Telegram)
    if len(text) > 4096:
        text = text[:4090] + "\n(...)"

    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'Markdown' 
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(TELEGRAM_API_URL, data=payload, timeout=10)

        if response.status_code == 200:
            print("✅ Mensagem enviada ao Telegram com sucesso!")
            return True
        else:
            print(f"❌ Erro API Telegram: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Exceção ao enviar mensagem para o Telegram: {e}")
        return False
