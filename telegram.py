# Nome do arquivo: telegram.py
# Versão: 2.0 (Envio Fatiado para mensagens longas > 4096 caracteres)

import httpx
import os
import json
import asyncio

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

def split_message(text: str, max_length: int = 4000) -> list:
    """
    Divide uma mensagem longa em partes menores.
    Tenta quebrar em quebras de linha duplas para não estragar a formatação de tópicos.
    """
    if len(text) <= max_length:
        return [text]
    
    parts = []
    while len(text) > 0:
        if len(text) <= max_length:
            parts.append(text)
            break
            
        # 1. Tenta quebrar no último parágrafo duplo
        split_index = text.rfind('\n\n', 0, max_length)
        
        # 2. Se não achar, tenta quebra de linha simples
        if split_index == -1:
            split_index = text.rfind('\n', 0, max_length)
            
        # 3. Se ainda não achar, quebra no último espaço em branco
        if split_index == -1:
            split_index = text.rfind(' ', 0, max_length)
            
        # 4. Último recurso (palavra gigante ou link gigantesco)
        if split_index == -1:
            split_index = max_length
            
        # Guarda a parte e avança o texto
        parts.append(text[:split_index].strip())
        text = text[split_index:].strip()
        
    return parts


async def send_telegram_message(text: str) -> bool:
    """
    Envia uma mensagem de texto formatada para o grupo do Telegram.
    Se for muito grande, divide em partes. Se falhar formatação, tenta texto puro.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Erro Telegram: Token ou Chat ID não encontrados (Verifique ENV ou config.json).")
        return False

    # Pica a mensagem usando a nossa nova função
    chunks = split_message(text)
    total_chunks = len(chunks)
    sucesso_geral = True

    try:
        # Abre a conexão com o Telegram
        async with httpx.AsyncClient() as client:
            
            for i, chunk in enumerate(chunks):
                # Opcional: Avisa se a mensagem for continuação
                texto_enviar = chunk
                if total_chunks > 1 and i > 0:
                    texto_enviar = f"*(Continuação {i+1}/{total_chunks})*\n\n" + chunk

                payload = {
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': texto_enviar,
                    'parse_mode': 'Markdown' 
                }

                response = await client.post(TELEGRAM_API_URL, data=payload, timeout=10)

                if response.status_code == 200:
                    print(f"✅ Mensagem enviada ao Telegram ({i+1}/{total_chunks}) com sucesso!")
                    
                # SE DEU ERRO DE FORMATAÇÃO (400 - can't parse entities)
                elif response.status_code == 400 and "can't parse entities" in response.text:
                    print(f"⚠️ Erro de formatação no Telegram ({i+1}/{total_chunks}). Reenviando como texto puro...")
                    
                    payload_fallback = {
                        'chat_id': TELEGRAM_CHAT_ID,
                        'text': texto_enviar
                    }
                    response_fallback = await client.post(TELEGRAM_API_URL, data=payload_fallback, timeout=10)
                    
                    if response_fallback.status_code == 200:
                        print(f"✅ Mensagem enviada ao Telegram em texto puro ({i+1}/{total_chunks})!")
                    else:
                        print(f"❌ Erro API Telegram (Fallback): {response_fallback.status_code} - {response_fallback.text}")
                        sucesso_geral = False
                else:
                    print(f"❌ Erro API Telegram ({i+1}/{total_chunks}): {response.status_code} - {response.text}")
                    sucesso_geral = False

                # Pausa de 1.5 segundos entre as partes para não ser bloqueado por spam
                if i < total_chunks - 1:
                    await asyncio.sleep(1.5)

        return sucesso_geral
                
    except Exception as e:
        print(f"❌ Exceção ao enviar mensagem para o Telegram: {e}")
        return False
