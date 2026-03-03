# Nome do arquivo: check_legislativo.py
# Versão: 10.5 (Fix: Senado NullSafe + Tramitações + Busca Robusta)

import os
import json
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Set, Dict, Tuple, Union

# --- CORREÇÃO DE IMPORTAÇÃO (DEBUG) ---
try:
    from telegram import send_telegram_message
except Exception as e:
    print(f"❌ ERRO CRÍTICO AO IMPORTAR TELEGRAM NO LEGISLATIVO: {e}")
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
TRACKING_FILE   = _resolve_data_path("LEG_TRACKING_FILE_PATH", "legislativo_watchlist.json")

KEYWORDS = [
    "marinha", "forças armadas", "defesa", "submarino",
    "amazônia azul", "prosub", "tamandaré", "fundo naval",
    "base industrial de defesa", "autoridade marítima", "emgepron",
    "ctmsp", "amazul", "teto de gastos", "arcabouço", "meta fiscal"
]
SENADO_SIGLAS = ["PLN", "PL", "PEC", "MPV", "PDL"]
CAMARA_SIGLAS  = ["PL", "PLP", "PEC", "MPV", "PLN"]

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

# =====================================================================================
# FIX CENTRAL: _safe_get_list
#
# A API do Senado retorna {"Materias": null} (None) quando não há resultados,
# em vez de {"Materias": {"Materia": []}}. Qualquer cadeia
# .get("Materias", {}).get("Materia", []) chama .get() sobre None → AttributeError
# silencioso, como se não houvesse nenhum resultado.
#
# Esta função resolve navegando com segurança:
#   - Retorna [] se qualquer nível for None ou não for dict
#   - Converte dict único em [dict] (API retorna objeto, não lista, quando há 1 resultado)
# =====================================================================================
def _safe_get_list(data: dict, *keys) -> list:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return []
        current = current.get(key)
        if current is None:
            return []
    if isinstance(current, list):
        return current
    if isinstance(current, dict):
        return [current]
    return []

async def _fetch_senado_ementa(client: httpx.AsyncClient, codigo: str) -> str:
    """Busca ementa no endpoint de detalhes quando a pesquisa básica retorna vazio."""
    try:
        url  = f"https://legis.senado.leg.br/dadosabertos/materia/{codigo}"
        resp = await client.get(url, headers={"Accept": "application/json"}, timeout=10)
        if resp.status_code == 200:
            d = _safe_get_list(resp.json(), "DetalheMateria", "Materia")
            if d:
                ementa = (d[0].get("DadosBasicosMateria", {}) or {}).get("EmentaMateria", "")
                if ementa:
                    return ementa.strip()
    except Exception as e:
        print(f"[_fetch_senado_ementa] Erro para código {codigo}: {e}")
    return ""

# --- API HELPERS ---
async def check_camara(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    results = []
    dt_inicio = (datetime.now() - timedelta(days=days_back_int)).strftime("%Y-%m-%d")
    dt_fim    = datetime.now().strftime("%Y-%m-%d")
    params  = {"dataApresentacaoInicio": dt_inicio, "dataApresentacaoFim": dt_fim,
               "itens": 100, "ordem": "DESC", "ordenarPor": "id"}
    headers = {"Accept": "application/json", "User-Agent": "MonitorLegislativoMB/1.0"}

    try:
        resp = await client.get(URL_CAMARA, params=params, headers=headers, timeout=20)
        if resp.status_code == 200:
            itens = resp.json().get("dados", []) or []
            for item in itens:
                if item.get("siglaTipo") not in CAMARA_SIGLAS: continue
                ementa   = item.get("ementa", "")
                found_kw = is_relevant(ementa)
                if found_kw:
                    results.append({
                        "uid":     f"CAM_{item.get('id')}",
                        "casa":    "Câmara",
                        "tipo":    item.get("siglaTipo"),
                        "numero":  str(item.get("numero")),
                        "ano":     str(item.get("ano")),
                        "ementa":  ementa,
                        "link":    f"https://www.camara.leg.br/propostas-legislativas/{item.get('id')}",
                        "keyword": found_kw,
                        "data":    item.get("dataApresentacao")
                    })
        else:
            print(f"[Câmara] HTTP {resp.status_code}")
    except Exception as e:
        print(f"[Câmara] Erro: {e}")
    return results


async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    results    = []
    headers    = {"Accept": "application/json", "User-Agent": "MonitorLegislativoMB/1.0"}
    ano_atual  = datetime.now().year
    limit_date = datetime.now() - timedelta(days=days_back_int + 2)

    for sigla in SENADO_SIGLAS:
        try:
            # FIX: usa httpx params em vez de f-string para encoding correto
            resp = await client.get(
                URL_SENADO,
                params={"sigla": sigla, "ano": ano_atual},
                headers=headers,
                timeout=20
            )

            if resp.status_code != 200:
                print(f"[Senado] HTTP {resp.status_code} para sigla {sigla}")
                continue

            # FIX: _safe_get_list evita AttributeError quando Materias=null
            raw_list = _safe_get_list(resp.json(), "PesquisaBasicaMateria", "Materias", "Materia")
            print(f"[Senado] {sigla}/{ano_atual}: {len(raw_list)} matérias na API")

            for mat in raw_list:
                dados    = mat.get("DadosBasicosMateria", {}) or {}
                data_str = dados.get("DataApresentacao")
                if not data_str: continue
                try:
                    if datetime.strptime(str(data_str)[:10], "%Y-%m-%d") < limit_date: continue
                except: continue

                ementa    = dados.get("EmentaMateria", "") or ""
                full_text = f"{ementa} {dados.get('NaturezaMateria', '') or ''}"
                found_kw  = is_relevant(full_text)
                if found_kw:
                    codigo = dados.get("CodigoMateria")
                    results.append({
                        "uid":     f"SEN_{codigo}",
                        "casa":    "Senado",
                        "tipo":    dados.get("SiglaMateria") or sigla,
                        "numero":  str(dados.get("NumeroMateria") or ""),
                        "ano":     str(dados.get("AnoMateria") or ano_atual),
                        "ementa":  ementa,
                        "link":    f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{codigo}",
                        "keyword": found_kw,
                        "data":    data_str
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

    new_items  = []
    ids_to_add = set()
    for p in all_proposals:
        if p['uid'] not in processed_ids:
            new_items.append(p)
            ids_to_add.add(p['uid'])

    if only_new:
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
    updates   = []

    async with httpx.AsyncClient(timeout=15) as client:
        for uid, info in watchlist.items():
            try:
                novo_status = None

                if info['casa'] == 'Câmara':
                    url  = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{info['id_api']}/tramitacoes"
                    resp = await client.get(url, headers={"Accept": "application/json"})
                    if resp.status_code == 200:
                        d = resp.json().get('dados', []) or []
                        if d:
                            ultimo   = d[-1]
                            despacho = ultimo.get('despacho') or ultimo.get('descricaoTramitacao') or "Sem descrição"
                            novo_status = f"{str(ultimo.get('dataHora', ''))[:10]}: {despacho}"

                elif info['casa'] == 'Senado':
                    url  = f"https://legis.senado.leg.br/dadosabertos/materia/movimentacoes/{info['id_api']}"
                    resp = await client.get(url, headers={"Accept": "application/json"})

                    if resp.status_code == 200:
                        # FIX: _safe_get_list para estrutura aninhada do Senado
                        movs = _safe_get_list(
                            resp.json(),
                            "MovimentacaoMateria", "Materia", "Tramitacoes", "Tramitacao"
                        )
                        if movs:
                            last = movs[-1]
                            # FIX: fallbacks para cobrir variações de campo entre versões da API
                            descricao = (
                                (last.get("IdentificacaoTramitacao") or {}).get("DescricaoSituacao")
                                or last.get("TextoTramitacao")
                                or last.get("DescricaoSituacao")
                                or "Sem descrição"
                            )
                            data_tram   = last.get("DataTramitacao") or ""
                            novo_status = f"{str(data_tram)[:10]}: {descricao}"
                    else:
                        print(f"[WatchlistSenado] HTTP {resp.status_code} para {uid}")

                if novo_status and novo_status != info.get('last_status'):
                    info['last_status'] = novo_status
                    updates.append({
                        "uid":    uid,
                        "titulo": f"{info.get('sigla','?')} {info.get('numero','?')}/{info.get('ano','?')}",
                        "status": novo_status,
                        "link":   info.get('link', ''),
                        "ementa": info.get('ementa', '')
                    })

            except Exception as e:
                print(f"Erro watch {uid}: {e}")

    if commit and updates:
        save_watchlist(watchlist)
        return updates          # Retorna só lista (compatível com API)

    if not commit:
        return updates, watchlist   # Retorna tupla (para o Robô tratar)

    return updates

# --- TRACKING MANUAL ---
def toggle_tracking(item_data: Dict) -> str:
    watchlist = load_watchlist()
    uid = str(item_data.get('uid', ''))

    if uid in watchlist:
        del watchlist[uid]
        save_watchlist(watchlist)
        return "removido"

    # FIX: split com maxsplit=1 para IDs que contenham underscore no valor
    id_api = uid.split('_', 1)[1] if '_' in uid else uid

    watchlist[uid] = {
        "casa":        item_data.get('casa'),
        "id_api":      id_api,
        "sigla":       item_data.get('tipo') or item_data.get('sigla', ''),
        "numero":      str(item_data.get('numero', '')),
        "ano":         str(item_data.get('ano', '')),
        "ementa":      item_data.get('ementa', 'Sem ementa disponível'),
        "link":        item_data.get('link', ''),
        "last_status": "Monitoramento Iniciado"
    }
    save_watchlist(watchlist)
    return "adicionado"


async def find_proposition(casa: str, sigla: str, numero: str, ano: str) -> Dict:
    async with httpx.AsyncClient(timeout=15) as client:

        if casa == 'Câmara':
            try:
                resp = await client.get(
                    URL_CAMARA,
                    params={"siglaTipo": sigla, "numero": numero, "ano": ano, "ordem": "DESC", "ordenarPor": "id"},
                    headers={"Accept": "application/json"}
                )
                if resp.status_code == 200:
                    dados = resp.json().get('dados') or []
                    if dados:
                        i = dados[0]
                        return {
                            "uid":         f"CAM_{i['id']}",
                            "casa":        "Câmara",
                            "tipo":        i.get('siglaTipo', sigla),
                            "numero":      str(i.get('numero', numero)),
                            "ano":         str(i.get('ano', ano)),
                            "ementa":      i.get('ementa', 'Sem ementa disponível'),
                            "link":        f"https://www.camara.leg.br/propostas-legislativas/{i['id']}",
                            "last_status": "Manual"
                        }
                print(f"[find_proposition/Câmara] HTTP {resp.status_code} ou sem dados")
            except Exception as e:
                print(f"[find_proposition/Câmara] Erro: {e}")

elif casa == 'Senado':
            try:
                resp = await client.get(
                    URL_SENADO,
                    params={"sigla": sigla, "numero": numero, "ano": ano},
                    headers={"Accept": "application/json"}
                )
                print(f"[find_proposition/Senado] HTTP {resp.status_code} para {sigla} {numero}/{ano}")

                if resp.status_code == 200:
                    l = _safe_get_list(resp.json(), "PesquisaBasicaMateria", "Materias", "Materia")
                    print(f"[find_proposition/Senado] {len(l)} resultado(s)")

                    if l:
                        d      = l[0].get('DadosBasicosMateria', {}) or {}
                        codigo = str(d.get('CodigoMateria', ''))

                        # Tenta pegar ementa da pesquisa básica
                        ementa = d.get('EmentaMateria') or ""

                        # FIX: se ementa vazia, busca no endpoint de detalhes
                        if not ementa.strip() and codigo:
                            print(f"[find_proposition/Senado] Ementa vazia, buscando detalhes para código {codigo}...")
                            ementa = await _fetch_senado_ementa(client, codigo)

                        return {
                            "uid":         f"SEN_{codigo}",
                            "casa":        "Senado",
                            "tipo":        d.get('SiglaMateria', sigla),
                            "numero":      str(d.get('NumeroMateria', numero)),
                            "ano":         str(d.get('AnoMateria', ano)),
                            "ementa":      ementa or 'Sem ementa disponível',
                            "link":        f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{codigo}",
                            "last_status": "Manual"
                        }
                    else:
                        print(f"[find_proposition/Senado] Nenhum resultado para {sigla} {numero}/{ano}")

            except Exception as e:
                print(f"[find_proposition/Senado] Erro: {e}")

# --- ROTINA AUTOMÁTICA (ROBÔ) ---
async def rotina_legislativa_completa():
    print(">>> Iniciando Rotina Legislativa (Modo Seguro)...")

    # 1. Novas Proposições (commit=False para não salvar se falhar envio)
    new_items = await check_and_process_legislativo(only_new=True, days_back=3, commit=False)

    if new_items:
        print(f"Novas proposições encontradas: {len(new_items)}")
        msg = ["🏛️ *Monitoramento Legislativo (Novidades)*"]
        for p in new_items:
            msg.append(
                f"\n📍 *{p['casa']}* - {p['tipo']} {p['numero']}/{p['ano']}\n"
                f"🔎 Tema: {p['keyword']}\n"
                f"📝 {p['ementa'][:120]}...\n"
                f"🔗 [Inteiro Teor]({p['link']})"
            )

        if await send_telegram_message("\n".join(msg)):
            current_ids = load_state()
            for p in new_items: current_ids.add(p['uid'])
            save_state(current_ids)
            print("✅ Estado salvo após envio.")
        else:
            print("❌ Falha no Telegram. Estado NÃO salvo.")

    # 2. Watchlist (commit=False)
    res = await check_tramitacoes_watchlist(commit=False)
    updates, watchlist_updated = res   # desempacota a tupla

    if updates:
        print(f"Watchlist updates: {len(updates)}")
        msg = ["🏛️ *Atualização de Tramitação (Watchlist)*", ""]
        for up in updates:
            msg.append(
                f"📌 *{up['titulo']}*\n"
                f"📝 {up['ementa'][:100]}...\n"
                f"🔄 Status: {up['status']}\n"
                f"🔗 [Link]({up['link']})\n"
            )

        if await send_telegram_message("\n".join(msg)):
            save_watchlist(watchlist_updated)
            print("✅ Watchlist atualizada após envio.")
        else:
            print("❌ Falha no Telegram (Watchlist).")
