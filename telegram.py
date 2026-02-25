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
    Se a formatação falhar, tenta enviar como texto puro.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Erro Telegram: Token ou Chat ID não encontrados (Verifique ENV ou config.json).")
        return False

    # Limita a mensagem a 4096 caracteres (limite do Telegram)
    if len(text) > 4096:
        text = text[:4090] + "\n(...)"

    # Tenta enviar com formatação Markdown (padrão)
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'Markdown' 
    }

    try:
        # Abre a conexão com o Telegram
        async with httpx.AsyncClient() as client:
            response = await client.post(TELEGRAM_API_URL, data=payload, timeout=10)

            if response.status_code == 200:
                print("✅ Mensagem enviada ao Telegram com sucesso!")
                return True
                
            # SE DEU ERRO DE FORMATAÇÃO (400 - can't parse entities)
            # A verificação agora ocorre DENTRO do bloco 'async with' (Conexão aberta)
            elif response.status_code == 400 and "can't parse entities" in response.text:
                print(f"⚠️ Erro de formatação no Telegram (Markdown quebrado). Reenviando como texto puro...")
                
                # Remove o parse_mode e envia de novo para garantir a entrega
                payload_fallback = {
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': text
                }
                response_fallback = await client.post(TELEGRAM_API_URL, data=payload_fallback, timeout=10)
                
                if response_fallback.status_code == 200:
                    print("✅ Mensagem enviada ao Telegram (em texto puro) com sucesso!")
                    return True
                else:
                    print(f"❌ Erro API Telegram (Fallback): {response_fallback.status_code} - {response_fallback.text}")
                    return False
            else:
                print(f"❌ Erro API Telegram: {response.status_code} - {response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Exceção ao enviar mensagem para o Telegram: {e}")
        return False
