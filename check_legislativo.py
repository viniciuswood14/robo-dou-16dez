# Nome do arquivo: check_legislativo.py
# Versão: 10.2 (Fix: API Compatibility + Safe Telegram State)

import os
import json
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Set, Dict, Tuple, Union

# Mudei para ver o erro real nos logs do Render
try:
    from telegram import send_telegram_message
except Exception as e:
    print(f"❌ ERRO CRÍTICO AO IMPORTAR TELEGRAM: {e}")
    # Mantemos o mock apenas para o código não travar totalmente, mas agora saberemos o motivo
    async def send_telegram_message(msg):
        print(f"[TELEGRAM MOCK - IMPORT FALHOU] {msg}")
        return False

# --- CONFIGURAÇÃO ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _resolve_data_path(env_var: str, default_filename: str) -> str:
    custom_path = os.environ.get(env_var)
    if custom_path:
        return custom_path
    return os.path.join(BASE_DIR, default_filename)

STATE_FILE_PATH = _resolve_data_path("LEG_STATE_FILE_PATH", "legislativo_state2.json")
TRACKING_FILE = _resolve_data_path("LEG_TRACKING_FILE_PATH", "legislativo_watchlist.json")

# Palavras-chave e Siglas
KEYWORDS = [
    "marinha", "forças armadas", "defesa", "submarino",  
    "amazônia azul", "prosub", "tamandaré", "fundo naval", 
    "base industrial de defesa", "autoridade marítima", "emgepron",
    "ctmsp", "amazul", "teto de gastos", "arcabouço", "meta fiscal"
]
SENADO_SIGLAS = ["PLN", "PL", "PEC", "MPV", "PDL"]
CAMARA_SIGLAS = ["PL", "PLP", "PEC", "MPV", "PLN"]

URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"

# --- PERSISTÊNCIA ---
def load_state() -> Set[str]:
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except:
        return set()

def save_state(processed_ids: Set[str]):
    try:
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(list(processed_ids), f)
    except:
        pass

def load_watchlist() -> Dict:
    try:
        with open(TRACKING_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_watchlist(data: Dict):
    with open(TRACKING_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

# --- FUNÇÃO DE FILTRO ---
def is_relevant(text: str) -> str:
    if not text: return None
    text_lower = text.lower()
    for kw in KEYWORDS:
        if kw in text_lower:
            return kw.upper()
    return None

# --- API HELPERS ---
async def check_camara(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    results = []
    dt_inicio = (datetime.now() - timedelta(days=days_back_int)).strftime("%Y-%m-%d")
    dt_fim = datetime.now().strftime("%Y-%m-%d")
    params = {"dataApresentacaoInicio": dt_inicio, "dataApresentacaoFim": dt_fim, "itens": 100, "ordem": "DESC", "ordenarPor": "id"}
    headers = {"Accept": "application/json", "User-Agent": "MonitorLegislativoMB/1.0"}

    try:
        resp = await client.get(URL_CAMARA, params=params, headers=headers, timeout=20)
        if resp.status_code == 200:
            itens = resp.json().get("dados", [])
            for item in itens:
                if item.get("siglaTipo") not in CAMARA_SIGLAS: continue
                ementa = item.get("ementa", "")
                found_kw = is_relevant(ementa)
                if found_kw:
                    results.append({
                        "uid": f"CAM_{item.get('id')}",
                        "casa": "Câmara",
                        "tipo": item.get("siglaTipo"),
                        "numero": str(item.get("numero")),
                        "ano": str(item.get("ano")),
                        "ementa": ementa,
                        "link": f"https://www.camara.leg.br/propostas-legislativas/{item.get('id')}",
                        "keyword": found_kw,
                        "data": item.get("dataApresentacao")
                    })
    except Exception as e:
        print(f"[Câmara] Erro: {e}")
    return results

async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    results = []
    headers = {"Accept": "application/json", "User-Agent": "MonitorLegislativoMB/1.0"}
    ano_atual = datetime.now().year
    limit_date = datetime.now() - timedelta(days=days_back_int + 2)

    for sigla in SENADO_SIGLAS:
        try:
            url = f"{URL_SENADO}?sigla={sigla}&ano={ano_atual}"
            resp = await client.get(url, headers=headers, timeout=20)
            if resp.status_code == 200:
                raw_list = resp.json().get("PesquisaBasicaMateria", {}).get("Materias", {}).get("Materia", [])
                if isinstance(raw_list, dict): raw_list = [raw_list]
                for mat in raw_list:
                    dados = mat.get("DadosBasicosMateria", {})
                    data_str = dados.get("DataApresentacao")
                    if not data_str: continue
                    try:
                        if datetime.strptime(str(data_str)[:10], "%Y-%m-%d") < limit_date: continue
                    except: continue
                    
                    ementa = dados.get("EmentaMateria", "")
                    full_text = f"{ementa} {dados.get('NaturezaMateria', '')}"
                    found_kw = is_relevant(full_text)
                    if found_kw:
                        results.append({
                            "uid": f"SEN_{dados.get('CodigoMateria')}",
                            "casa": "Senado",
                            "tipo": dados.get("SiglaMateria"),
                            "numero": str(dados.get("NumeroMateria")),
                            "ano": str(dados.get("AnoMateria")),
                            "ementa": ementa,
                            "link": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{dados.get('CodigoMateria')}",
                            "keyword": found_kw,
                            "data": data_str
                        })
        except Exception as e:
            print(f"[Senado] Erro {sigla}: {e}")
    return results

# --- CORE FUNCTIONS ---

async def check_and_process_legislativo(only_new: bool = True, days_back: int = 5, commit: bool = True) -> List[Dict]:
    """
    commit=True: Salva o estado imediatamente (Comportamento padrão da API/Site).
    commit=False: Não salva o estado (Usado pelo Robô para confirmar envio antes de salvar).
    """
    processed_ids = load_state()
    all_proposals = []

    async with httpx.AsyncClient(timeout=30) as client:
        r = await asyncio.gather(check_camara(client, days_back), check_senado(client, days_back))
        all_proposals = r[0] + r[1]

    all_proposals.sort(key=lambda x: x.get('data', ''), reverse=True)

    new_items = []
    ids_to_add = set()
    for p in all_proposals:
        if p['uid'] not in processed_ids:
            new_items.append(p)
            ids_to_add.add(p['uid'])

    if only_new:
        # Se for para salvar imediatamente (API ou script simples)
        if commit and new_items:
            processed_ids.update(ids_to_add)
            save_state(processed_ids)
        return new_items
    
    return all_proposals


async def check_tramitacoes_watchlist(commit: bool = True) -> Union[List[Dict], Tuple[List[Dict], Dict]]:
    """
    Se commit=True (Botão do Site): Salva e retorna APENAS a lista de updates.
    Se commit=False (Robô): Retorna (updates, nova_watchlist) para salvar depois.
    """
    watchlist = load_watchlist()
    updates = []
    
    async with httpx.AsyncClient(timeout=10) as client:
        for uid, info in watchlist.items():
            try:
                novo_status = None
                if info['casa'] == 'Câmara':
                    url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{info['id_api']}/tramitacoes"
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        d = resp.json().get('dados', [])
                        if d: novo_status = f"{d[-1].get('dataHora', '')[:10]}: {d[-1].get('despacho') or d[-1].get('descricaoTramitacao')}"
                
                elif info['casa'] == 'Senado':
                    url = f"https://legis.senado.leg.br/dadosabertos/materia/movimentacoes/{info['id_api']}"
                    resp = await client.get(url, headers={"Accept": "application/json"})
                    if resp.status_code == 200:
                        movs = resp.json().get('MovimentacaoMateria', {}).get('Materia', {}).get('Tramitacoes', {}).get('Tramitacao', [])
                        if isinstance(movs, dict): movs = [movs]
                        if movs: 
                            last = movs[0]
                            novo_status = f"{last.get('DataTramitacao')}: {last.get('IdentificacaoTramitacao', {}).get('DescricaoSituacao') or last.get('TextoTramitacao')}"

                if novo_status and novo_status != info.get('last_status'):
                    info['last_status'] = novo_status
                    updates.append({
                        "uid": uid, "titulo": f"{info['sigla']} {info['numero']}/{info['ano']}",
                        "status": novo_status, "link": info['link'], "ementa": info['ementa']
                    })
            except Exception as e:
                print(f"Erro watch {uid}: {e}")

    if commit and updates:
        save_watchlist(watchlist)
        return updates  # Retorna só lista (Compatível API)
    
    if not commit:
        return updates, watchlist # Retorna tupla (Para o Robô tratar)
    
    return updates

# --- TRACKING MANUAL ---
def toggle_tracking(item_data: Dict) -> str:
    watchlist = load_watchlist()
    uid = item_data.get('uid')
    if uid in watchlist:
        del watchlist[uid]
        save_watchlist(watchlist)
        return "removido"
    watchlist[uid] = {
        "casa": item_data.get('casa'), "id_api": item_data.get('uid').split('_')[1],
        "sigla": item_data.get('tipo'), "numero": item_data.get('numero'), "ano": item_data.get('ano'),
        "ementa": item_data.get('ementa'), "link": item_data.get('link'), "last_status": "Monitoramento Iniciado"
    }
    save_watchlist(watchlist)
    return "adicionado"

async def find_proposition(casa: str, sigla: str, numero: str, ano: str) -> Dict:
    async with httpx.AsyncClient(timeout=15) as client:
        if casa == 'Câmara':
            url = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
            try:
                resp = await client.get(url, params={"siglaTipo": sigla, "numero": numero, "ano": ano, "ordem": "DESC", "ordenarPor": "id"})
                if resp.status_code == 200 and resp.json().get('dados'):
                    i = resp.json().get('dados')[0]
                    return {"uid": f"CAM_{i['id']}", "casa": "Câmara", "tipo": i['siglaTipo'], "numero": str(i['numero']), "ano": str(i['ano']), "ementa": i['ementa'], "link": f"https://www.camara.leg.br/propostas-legislativas/{i['id']}", "last_status": "Manual"}
            except: pass
        elif casa == 'Senado':
            url = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"
            try:
                resp = await client.get(url, headers={"Accept": "application/json"}, params={"sigla": sigla, "numero": numero, "ano": ano})
                if resp.status_code == 200:
                    l = resp.json().get('PesquisaBasicaMateria', {}).get('Materias', {}).get('Materia', [])
                    if isinstance(l, dict): l = [l]
                    if l:
                        d = l[0].get('DadosBasicosMateria', {})
                        return {"uid": f"SEN_{d['CodigoMateria']}", "casa": "Senado", "tipo": d['SiglaMateria'], "numero": str(d['NumeroMateria']), "ano": str(d['AnoMateria']), "ementa": d['EmentaMateria'], "link": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{d['CodigoMateria']}", "last_status": "Manual"}
            except: pass
    return None

# --- ROTINA AUTOMÁTICA (ROBÔ) ---
async def rotina_legislativa_completa():
    print(">>> Iniciando Rotina Legislativa (Modo Seguro)...")
    
    # 1. Novas Proposições (commit=False para não salvar se falhar envio)
    new_items = await check_and_process_legislativo(only_new=True, days_back=3, commit=False)
    
    if new_items:
        print(f"Novas proposições encontradas: {len(new_items)}")
        msg = ["🏛️ *Monitoramento Legislativo (Novidades)*"]
        for p in new_items:
            msg.append(f"\n📍 *{p['casa']}* - {p['tipo']} {p['numero']}/{p['ano']}\n🔎 Tema: {p['keyword']}\n📝 {p['ementa'][:120]}...\n🔗 [Inteiro Teor]({p['link']})")
        
        if await send_telegram_message("\n".join(msg)):
            # Sucesso no envio: Salva IDs
            current_ids = load_state()
            for p in new_items: current_ids.add(p['uid'])
            save_state(current_ids)
            print("✅ Estado salvo após envio.")
        else:
            print("❌ Falha no Telegram. Estado NÃO salvo.")

    # 2. Watchlist (commit=False)
    res = await check_tramitacoes_watchlist(commit=False)
    updates, watchlist_updated = res # Desempacota a tupla
    
    if updates:
        print(f"Watchlist updates: {len(updates)}")
        msg = ["🏛️ *Atualização de Tramitação (Watchlist)*", ""]
        for up in updates:
            msg.append(f"📌 *{up['titulo']}*\n📝 {up['ementa'][:100]}...\n🔄 Status: {up['status']}\n🔗 [Link]({up['link']})\n")
        
        if await send_telegram_message("\n".join(msg)):
            save_watchlist(watchlist_updated)
            print("✅ Watchlist atualizada após envio.")
        else:
            print("❌ Falha no Telegram (Watchlist).")
