## Nome do arquivo: run_check.py
# Versão: 19.1 (Add: Sincronização Automática Gmail -> GitHub + Fix Seção 2 e Ementa Score)

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo 
from typing import Dict, List, Any, Set
from bs4 import BeautifulSoup

# IA / Gemini
import google.generativeai as genai

# Importa as funções do Robô DOU (api.py)
try:
    from api import (
        inlabs_login_and_get_session,
        resolve_date_url,
        fetch_listing_html,
        pick_zip_links_from_listing,
        download_zip,
        extract_xml_from_zip,
        process_grouped_materia,
        get_ai_analysis,
        monta_whatsapp,
        GEMINI_API_KEY,
        GEMINI_MASTER_PROMPT,
        GEMINI_MPO_PROMPT,
        GEMINI_PESSOAL_PROMPT, # <- ADICIONADO NOVO PROMPT DE PESSOAL
        Publicacao
    )
except ImportError as e:
    print(f"Erro: Falha ao importar módulos do 'api.py'. Detalhe: {e}")
    raise

# Importa o sender do Telegram
try:
    from telegram import send_telegram_message
except ImportError:
    # Mock para não quebrar se o arquivo não existir ou falhar
    async def send_telegram_message(msg):
        print(f"[TELEGRAM MOCK] {msg}")

# Importa a função do robô Valor
try:
    from check_valor import check_and_process_valor
except ImportError:
    pass

# Importa a função do robô PAC
try:
    from check_pac import check_and_process_pac
except ImportError:
    pass

# --- [IMPORTAÇÃO FALLBACK] ---
try:
    from dou_fallback import executar_fallback
except ImportError:
    print("Aviso: 'dou_fallback.py' não encontrado. Redundância desativada.")
    executar_fallback = None

# --- [IMPORTAÇÃO LEGISLATIVO] ---
try:
    from check_legislativo import rotina_legislativa_completa
    print("✅ Módulo Legislativo (rotina_legislativa_completa) importado com sucesso!")
except ImportError as e:
    print(f"❌ ERRO CRÍTICO: Falha ao importar 'rotina_legislativa_completa'. Detalhe: {e}")
    rotina_legislativa_completa = None
except Exception as e:
    print(f"❌ ERRO DESCONHECIDO ao carregar legislativo: {e}")
    rotina_legislativa_completa = None

# --- [NOVO: IMPORTAÇÃO SYNC PAC (GMAIL)] ---
try:
    from sync_pac import sync_pac_routine
    print("✅ Módulo Sync PAC (Gmail) importado com sucesso!")
except ImportError:
    print("⚠️ Aviso: 'sync_pac.py' não encontrado. Sincronização automática desativada.")
    sync_pac_routine = None


# --- CONFIGURAÇÃO DO ESTADO (DOU) ---
STATE_FILE_PATH = os.environ.get("STATE_FILE_PATH", "/dados/processed_state.json")

def load_state() -> Dict[str, List[str]]:
    """Carrega o estado (ZIPs processados) do disco."""
    try:
        state_dir = os.path.dirname(STATE_FILE_PATH)
        if not os.path.exists(state_dir):
            os.makedirs(state_dir, exist_ok=True)
        
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {} 

def save_state(state: Dict[str, List[str]]):
    """Salva o estado atual no disco."""
    try:
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Erro Crítico ao salvar estado: {e}")


async def check_and_process_dou(today_str: str):
    """
    Função principal (MODO SEGURANÇA + HEARTBEAT).
    Avisa no Telegram mesmo se não encontrar nada.
    """
    print(f"--- Iniciando verificação do DOU para a data: {today_str} (SEM PARSER) ---")
    
    # 0. Configura IA
    if not GEMINI_API_KEY:
        print("Erro: GEMINI_API_KEY não encontrada.")
        await send_telegram_message(f"⚠️ Erro Crítico: GEMINI_API_KEY não configurada.")
        return
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        model = genai.GenerativeModel("gemini-3-pro-preview")
    except Exception as e:
        print(f"Falha IA: {e}")
        return

    state = load_state()
    processed_zips_today = set(state.get(today_str, []))
    
    # Flag para saber se usamos o fallback hoje para não repetir
    fallback_marker = f"FALLBACK_DONE_{today_str}"
    if fallback_marker in processed_zips_today:
        print("Modo Fallback já foi executado com sucesso hoje. Pulando.")
        return

    pubs_finais: List[Publicacao] = []
    usou_fallback = False
    sucesso_inlabs = False
    client = None

    # --- TENTATIVA 1: INLABS ---
    try:
        print(">>> Tentando conexão InLabs (Principal)...")
        client = await inlabs_login_and_get_session()
        listing_url = await resolve_date_url(client, today_str)
        html = await fetch_listing_html(client, today_str)
        all_zip_links = pick_zip_links_from_listing(html, listing_url, ["DO1", "DO2", "DO3"])
        
        if not all_zip_links:
            msg = f"🔎 Monitoramento DOU ({today_str}): Nenhum arquivo ZIP disponível no InLabs no momento."
            print(msg)
            await send_telegram_message(msg)
            return 

        # Filtra novos
        current_zip_set = set(all_zip_links)
        new_zip_links = list(current_zip_set - processed_zips_today)

        if not new_zip_links:
            msg = f"✅ Monitoramento DOU ({today_str}): Nenhuma nova edição lançada desde a última verificação."
            print(msg)
            await send_telegram_message(msg)
            return

        print(f"Encontrados {len(new_zip_links)} novos arquivos ZIP.")
        await send_telegram_message(f"📥 Baixando {len(new_zip_links)} novos arquivos ZIP do DOU...")
        
        # Processa ZIPs (Extração Bruta)
        all_new_xml_blobs = []
        for zurl in new_zip_links:
            print(f"Baixando {zurl}...")
            zb = await download_zip(client, zurl)
            all_new_xml_blobs.extend(extract_xml_from_zip(zb))
        
        if not all_new_xml_blobs:
            msg = f"⚠️ Monitoramento DOU ({today_str}): ZIPs baixados, mas parecem vazios ou sem XML."
            print(msg)
            await send_telegram_message(msg)
            state[today_str] = list(current_zip_set)
            save_state(state)
            return

        # Agrupa e Filtra (Lógica com Ementa Score - Idêntica ao api.py)
        materias = {}
        for blob in all_new_xml_blobs:
            try:
                soup = BeautifulSoup(blob, "lxml-xml")
                article = soup.find("article")
                if not article: continue
                
                materia_id = article.get("idMateria")
                if not materia_id: continue
                
                # O dicionário agora guarda um 'score' para saber qual arquivo tem a maior Ementa
                if materia_id not in materias:
                    materias[materia_id] = {"main_article": None, "full_text": "", "score": -1}
                    
                materias[materia_id]["full_text"] += (blob.decode("utf-8", errors="ignore") + "\n")
                
                body = article.find("body")
                if body:
                    # Lê as tags REAIS usando o BeautifulSoup
                    ementa_tag = article.find("Ementa")
                    identifica_tag = article.find("Identifica")
                    
                    # Pega apenas o texto puro (ignorando tags e CDATA)
                    ementa_text = ementa_tag.text.strip() if ementa_tag and ementa_tag.text else ""
                    identifica_text = identifica_tag.text.strip() if identifica_tag and identifica_tag.text else ""
                    
                    # Soma a quantidade de letras úteis
                    score = len(ementa_text) + len(identifica_text)
                    
                    # Se este arquivo tiver um texto de Ementa MAIOR que o anterior, ele assume como principal!
                    if materias[materia_id]["main_article"] is None or score > materias[materia_id]["score"]:
                        materias[materia_id]["main_article"] = article
                        materias[materia_id]["score"] = score
            except: continue
        
        for materia_id, content in materias.items():
            if content["main_article"]:
                publication = process_grouped_materia(
                    content["main_article"], content["full_text"], custom_keywords=[]
                )
                if publication:
                    pubs_finais.append(publication)

        # Deduplicar Geral
        seen = set()
        unique_pubs = []
        for p in pubs_finais:
            key = (p.organ or "") + "||" + (p.type or "") + "||" + (p.summary or "")[:100]
            if key not in seen:
                seen.add(key)
                unique_pubs.append(p)
        pubs_finais = unique_pubs
        
        sucesso_inlabs = True
        state[today_str] = list(current_zip_set)

    except Exception as e:
        print(f"⚠️ Erro no InLabs: {e}")
        usou_fallback = True
        
    finally:
        if client: await client.aclose()

    # --- TENTATIVA 2: FALLBACK (Se InLabs falhou) ---
    if usou_fallback and executar_fallback:
        print(">>> Iniciando Modo de Redundância (Fallback)...")
        await send_telegram_message("⚠️ InLabs instável. Iniciando busca redundante (Fallback)...")
        try:
            res_fallback = await executar_fallback(today_str, [])
            
            if res_fallback:
                print(f"Fallback encontrou {len(res_fallback)} itens.")
                for item in res_fallback:
                    p = Publicacao(
                        organ=item['organ'],
                        type=item['type'],
                        summary=item['summary'],
                        raw=item['raw'],
                        relevance_reason=item['relevance_reason'],
                        section=item['section'],
                        clean_text=item['raw'],
                        is_parsed_mpo=False # Fallback é genérico
                    )
                    pubs_finais.append(p)
                
                current_list = state.get(today_str, [])
                current_list.append(fallback_marker)
                state[today_str] = current_list
            else:
                msg = "⚠️ Fallback: Busca realizada, mas nada relevante encontrado."
                print(msg)
                await send_telegram_message(msg)

        except Exception as ef:
            print(f"Erro CRÍTICO: Falha também no Fallback: {ef}")
            await send_telegram_message(f"❌ Erro Crítico Total (InLabs + Fallback): {ef}")
            return

    # --- ANÁLISE COM IA (Todas as publicações) ---
    if not pubs_finais:
        if sucesso_inlabs: save_state(state)
        msg = f"ℹ️ Monitoramento DOU ({today_str}): Arquivos processados, mas nenhuma matéria passou pelos filtros de Keywords."
        print(msg)
        await send_telegram_message(msg)
        return

    print(f"Enviando {len(pubs_finais)} matérias para análise da IA...")
    
    pubs_ready = []
    tasks = []

    for p in pubs_finais:
        # Mesma lógica do api.py: Identifica Seção 2 e injeta o alvo
        if p.section and ("DO2" in p.section or "Seção 2" in p.section):
            alvo_identificado = p.relevance_reason or "Militar/Servidor de interesse"
            prompt_to_use = GEMINI_PESSOAL_PROMPT + f"\n\n[ALVO IDENTIFICADO PELO SISTEMA: {alvo_identificado}]\nDescreva apenas a ação referente a este alvo."
        elif p.is_mpo_navy_hit:
            prompt_to_use = GEMINI_MPO_PROMPT
        else:
            prompt_to_use = GEMINI_MASTER_PROMPT
        
        texto_analise = p.clean_text if p.clean_text else p.raw
        tasks.append(get_ai_analysis(texto_analise, model, prompt_to_use))

    ai_results = await asyncio.gather(*tasks, return_exceptions=True)

    for p, ai_out in zip(pubs_finais, ai_results):
        if isinstance(ai_out, Exception) or not ai_out:
            p.relevance_reason = "⚠️ IA indisponível. Verifique manualmente."
            pubs_ready.append(p)
            continue
        
        is_secao_2 = p.section and ("DO2" in p.section or "Seção 2" in p.section)
        
        # Filtro de relevância da IA (Agora BLINDADO para a Seção 2)
        if "sem impacto" in ai_out.lower() and not p.is_mpo_navy_hit and not is_secao_2:
            continue
            
        p.relevance_reason = ai_out
        pubs_ready.append(p)

    if not pubs_ready:
        if sucesso_inlabs: save_state(state)
        msg = f"🧠 IA Finalizada: Todas as matérias foram descartadas por falta de relevância/impacto."
        print(msg)
        await send_telegram_message(msg)
        return

    # --- ENVIO TELEGRAM (POSIIVO) ---
    texto_zap = monta_whatsapp(pubs_ready, today_str)
    
    header = f"Alerta de Publicações - DOU ({today_str})\n"
    if usou_fallback:
        header += "⚠️ *Aviso: Dados via portal público (InLabs instável).*\n"
    
    final_msg = header + "\n" + texto_zap
    
    await send_telegram_message(final_msg)
    
    # Salva o estado final
    save_state(state)
    print("Ciclo finalizado com sucesso.")


# --- LOOP PRINCIPAL ---
async def main_loop():
    """
    Loop de serviço contínuo.
    """
    TZ_BRASILIA = ZoneInfo("America/Sao_Paulo")
    INTERVALO_SEGUNDOS = 10 * 60 # 10 minutos
    
    valor_check_done = False
    pac_check_done = False
    pac_sync_done = False # <--- Flag de controle do Sync Gmail
    
    # Controle de hora para o Legislativo (para não rodar a cada 10 min)
    last_legis_hour = -1 
    
    last_day = None
    
    print("--- Robô Integrado (Safety Mode + Heartbeat) Iniciado ---")

    while True:
        agora = datetime.now(TZ_BRASILIA)
        hoje_str = agora.strftime('%Y-%m-%d')
        ano_str = agora.strftime('%Y')
        ontem_str = (agora - timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Reseta flags diárias
        if last_day != hoje_str:
            valor_check_done = False
            pac_check_done = False
            pac_sync_done = False # <--- Reseta a flag do Sync
            last_day = hoje_str
            print(f"*** Novo dia: {hoje_str} ***")

        # Horário de expediente EXPANDIDO (04h às 23h59)
        if 4 <= agora.hour <= 23:
            
            # 1. DOU (Roda a cada 10 min)
            try:
                await check_and_process_dou(hoje_str)
            except Exception as e:
                print(f"Erro no loop DOU: {e}")
                await send_telegram_message(f"❌ Erro Loop DOU: {e}")

            is_weekday = agora.weekday() < 5

            # 2. Valor (05:10+, dias úteis)
            if is_weekday and agora.hour == 5 and agora.minute >= 10 and not valor_check_done:
                try:
                    await check_and_process_valor(ontem_str)
                    valor_check_done = True
                except Exception as e:
                    print(f"Erro Valor: {e}")

            # 3. PAC (05:15+, dias úteis)
            if is_weekday and agora.hour == 5 and agora.minute >= 15 and not pac_check_done:
                try:
                    await check_and_process_pac(ano_str)
                    pac_check_done = True
                except Exception as e:
                    print(f"Erro PAC: {e}")

            # 4. SYNC PAC GMAIL (06:00+, dias úteis)
            # Roda depois do PAC check original ou para preparar o dia seguinte
            # Como atualiza o GitHub, vai triggerar um rebuild do Render
            if is_weekday and agora.hour == 7 and agora.minute >= 0 and not pac_sync_done:
                if sync_pac_routine:
                    print(f"[{agora.strftime('%H:%M')}] 🔄 Rodando Sincronização PAC (Gmail -> Github)...")
                    try:
                        # Executa a rotina (síncrona)
                        sync_pac_routine()
                        pac_sync_done = True
                        print("✅ Sync PAC finalizado.")
                    except Exception as e:
                        print(f"❌ Erro Sync PAC: {e}")

            # 5. LEGISLATIVO (Rodar 1 vez por hora, a partir das 08h)
            # Verifica se o módulo foi importado e se mudou a hora desde a última checagem
            if rotina_legislativa_completa and agora.hour != last_legis_hour:
                if agora.hour >= 8: # Evita rodar de madrugada quando não há sessões
                    try:
                        print(f"[{agora.strftime('%H:%M')}] Executando rotina legislativa...")
                        await rotina_legislativa_completa()
                        last_legis_hour = agora.hour # Atualiza flag para não rodar de novo nesta hora
                    except Exception as e:
                        print(f"Erro Legislativo: {e}")
                        # Opcional: await send_telegram_message(f"⚠️ Erro Legislativo: {e}")

        else:
            print(f"[{agora.strftime('%H:%M')}] Fora de expediente. Dormindo.")

        await asyncio.sleep(INTERVALO_SEGUNDOS)
