# Nome do arquivo: check_valor.py

import json
import os
import asyncio
from typing import Dict, Set, List, Any

# Nossas importações
from telegram import send_telegram_message

# Importações do Robô (reutilizando a função de análise)
try:
    from api import run_valor_analysis
except ImportError:
    print("Erro: Falha ao importar 'run_valor_analysis' do 'api.py'.")
    raise

# --- CONFIGURAÇÃO DO ESTADO ---
STATE_FILE_PATH = os.environ.get("VALOR_STATE_FILE_PATH", "/dados/valor_processed_links.json")


def load_valor_state() -> Set[str]:
    """Carrega os links já processados."""
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_valor_state(links: Set[str]):
    """Salva os links processados."""
    try:
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(list(links), f, indent=2)
    except Exception as e:
        print(f"Erro Crítico: Falha ao salvar estado do Valor: {e}")


async def check_and_process_valor(today_str: str):
    """
    Função chamada pelo AGENDADOR (run_check.py).
    Usa a análise da API, filtra pelo state e envia para o Telegram.
    """
    print(f"--- Iniciando verificação do Valor Econômico para: {today_str} ---")
    
    # 1. Carrega o estado (links já vistos)
    processed_links = load_valor_state()

    # 2. Roda a análise principal (importada da api.py, sem usar o state)
    #    A função retorna (lista_de_pubs, set_links_encontrados)
    all_pubs, all_links_found = await run_valor_analysis(today_str, use_state=False)
    
    if not all_pubs:
        print("--- Verificação do Valor finalizada, nenhuma publicação encontrada. ---")
        return

    # 3. Filtra apenas links novos E com impacto
    pubs_finais = []
    novos_links_para_salvar = set()
    
    for p in all_pubs:
        # Se o link ainda não foi processado (não está no state)
        if p['link'] not in processed_links:
            novos_links_para_salvar.add(p['link'])
            # E a IA não disse "sem impacto"
            if "sem impacto direto" not in p['analise_ia'].lower():
                pubs_finais.append(p)

    if not pubs_finais:
        print("--- Verificação do Valor finalizada, sem novidades para o Telegram. ---")
        # Salva os links novos que foram "sem impacto" para não re-processar
        if novos_links_para_salvar:
            save_valor_state(processed_links.union(novos_links_para_salvar))
        return

    # 4. Monta e envia a mensagem do Telegram
    lines = [f"📰 *Radar de Notícias - Defesa & Orçamento* ({today_str}):\n"]
    
    for p in pubs_finais:
        # Nota: O Telegram usa 'Markdown', não 'WhatsApp'
        lines.append(f"▶️ *Título:* {p['titulo']}")
        lines.append(f"📌 *Link:* {p['link']}")
        lines.append(f"⚓ *Análise IA:* {p['analise_ia']}")
        lines.append("") # Espaçamento

    message = "\n".join(lines)
    await send_telegram_message(message)
    
    # 5. Salva o estado final
    save_valor_state(processed_links.union(novos_links_para_salvar))
    print("--- Verificação do Valor finalizada com sucesso (enviado ao Telegram). ---")
