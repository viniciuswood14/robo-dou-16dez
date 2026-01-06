# Nome do arquivo: check_legislativo.py
# Versão: 10.0 (Production Mode - Monitoramento Ativo)

import os
import json
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Set, Dict

# Tenta importar o módulo de telegram, se não existir, cria um mock para não quebrar
try:
    from telegram import send_telegram_message
except ImportError:
    async def send_telegram_message(msg):
        print(f"[TELEGRAM MOCK] {msg}")

# --- CONFIGURAÇÃO ---
STATE_FILE_PATH = os.environ.get("LEG_STATE_FILE_PATH", "legislativo_state.json")

# Lista de palavras-chave estratégicas para a Marinha
KEYWORDS = [
    "marinha", "forças armadas", "defesa", "submarino",  
    "amazônia azul", "prosub", "tamandaré", "fundo naval", 
    "base industrial de defesa", "autoridade marítima", "emgepron",
    "ctmsp", "amazul", "teto de gastos", "arcabouço", "meta fiscal"
]

# Siglas de interesse
SENADO_SIGLAS = ["PLN", "PL", "PEC", "MPV", "PDL"]
CAMARA_SIGLAS = ["PL", "PLP", "PEC", "MPV", "PLN"]

URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"

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

# --- FUNÇÃO DE FILTRO LOCAL ---
def is_relevant(text: str) -> str:
    """Verifica se o texto contém alguma keyword e retorna a keyword encontrada."""
    if not text: return None
    text_lower = text.lower()
    for kw in KEYWORDS:
        if kw in text_lower:
            return kw.upper()
    return None

# --- CÂMARA DOS DEPUTADOS ---
async def check_camara(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    print(f">>> [API Câmara] Iniciando varredura ({days_back_int} dias)...")
    results = []
    
    # Define janela de tempo
    dt_inicio = (datetime.now() - timedelta(days=days_back_int)).strftime("%Y-%m-%d")
    dt_fim = datetime.now().strftime("%Y-%m-%d")
    
    params = {
        "dataApresentacaoInicio": dt_inicio,
        "dataApresentacaoFim": dt_fim,
        "itens": 100,
        "ordem": "DESC",
        "ordenarPor": "id"
    }
    
    # Cabeçalho para evitar bloqueio
    headers = {"Accept": "application/json", "User-Agent": "MonitorLegislativoMB/1.0"}

    try:
        resp = await client.get(URL_CAMARA, params=params, headers=headers, timeout=20)
        
        if resp.status_code != 200:
            print(f"   -> [Câmara] Erro API: {resp.status_code}")
            return []

        data = resp.json()
        itens = data.get("dados", [])
        print(f"   -> [Câmara] Analisando {len(itens)} itens recentes...")

        for item in itens:
            sigla = item.get("siglaTipo")
            if sigla not in CAMARA_SIGLAS:
                continue

            # A ementa na listagem inicial as vezes é curta, mas serve para filtro primário
            ementa = item.get("ementa", "")
            found_kw = is_relevant(ementa)
            
            if found_kw:
                uid = f"CAM_{item.get('id')}"
                results.append({
                    "uid": uid,
                    "casa": "Câmara",
                    "tipo": sigla,
                    "numero": str(item.get("numero")),
                    "ano": str(item.get("ano")),
                    "ementa": ementa,
                    "link": f"https://www.camara.leg.br/propostas-legislativas/{item.get('id')}",
                    "keyword": found_kw,
                    "data": item.get("dataApresentacao")
                })

    except Exception as e:
        print(f"   -> [Câmara] Exceção: {e}")

    return results

# --- SENADO FEDERAL ---
async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    print(f">>> [API Senado] Iniciando varredura ({days_back_int} dias)...")
    results = []
    headers = {"Accept": "application/json", "User-Agent": "MonitorLegislativoMB/1.0"}
    
    ano_atual = datetime.now().year
    limit_date = datetime.now() - timedelta(days=days_back_int + 2) # Margem de segurança

    # O Senado busca por Sigla + Ano. Varremos as siglas principais.
    for sigla in SENADO_SIGLAS:
        url = f"{URL_SENADO}?sigla={sigla}&ano={ano_atual}"
        try:
            resp = await client.get(url, headers=headers, timeout=20)
            if resp.status_code != 200:
                continue

            data = resp.json()
            # Navegação no JSON complexo do Senado
            raw_list = data.get("PesquisaBasicaMateria", {}).get("Materias", {}).get("Materia", [])
            if isinstance(raw_list, dict): raw_list = [raw_list]
            
            for mat in raw_list:
                dados = mat.get("DadosBasicosMateria", {})
                
                # Filtro de Data
                data_str = dados.get("DataApresentacao")
                if not data_str: continue
                try:
                    dt_obj = datetime.strptime(str(data_str)[:10], "%Y-%m-%d")
                    if dt_obj < limit_date: continue
                except: continue

                # Filtro de Conteúdo
                ementa = dados.get("EmentaMateria", "")
                natureza = dados.get("NaturezaMateria", "")
                full_text = f"{ementa} {natureza}"
                
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
            print(f"   -> [Senado] Erro na sigla {sigla}: {e}")

    return results

# --- MAIN ---
async def check_and_process_legislativo(only_new: bool = True, days_back: int = 5) -> List[Dict]:
    """
    Função principal chamada pelo robô (api.py).
    Se only_new=True, filtra pelo state e notifica no Telegram.
    Se only_new=False, retorna tudo (para o Dashboard no site).
    """
    processed_ids = load_state()
    all_proposals = []

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # Roda Câmara e Senado em paralelo
        task_cam = check_camara(client, days_back)
        task_sen = check_senado(client, days_back)
        
        results = await asyncio.gather(task_cam, task_sen)
        all_proposals.extend(results[0]) # Câmara
        all_proposals.extend(results[1]) # Senado

    # Ordena por data (mais recente primeiro)
    try:
        all_proposals.sort(key=lambda x: x.get('data', ''), reverse=True)
    except: pass

    # Separa novidades para notificação
    new_for_telegram = []
    for p in all_proposals:
        if p['uid'] not in processed_ids:
            new_for_telegram.append(p)
            processed_ids.add(p['uid'])

    # Salva estado atualizado
    save_state(processed_ids)

    # Notificação (apenas se solicitado)
    if only_new and new_for_telegram:
        print(f"Enviando {len(new_for_telegram)} novas proposições para o Telegram...")
        msg_header = "🏛️ *Monitoramento Legislativo (Novidades)*"
        
        # Envia em blocos para não estourar limite do Telegram
        buffer_msg = [msg_header]
        for p in new_for_telegram:
            item_txt = (
                f"\n📍 *{p['casa']}* - {p['tipo']} {p['numero']}/{p['ano']}"
                f"\n🔎 Tema: {p['keyword']}"
                f"\n📝 {p['ementa'][:150]}..."
                f"\n🔗 [Inteiro Teor]({p['link']})"
            )
            buffer_msg.append(item_txt)
        
        await send_telegram_message("\n".join(buffer_msg))
        return new_for_telegram

    # Se a chamada veio do SITE (only_new=False), retorna TUDO (novos + velhos)
    if not only_new:
        return all_proposals

    return new_for_telegram

# Adicione ao check_legislativo.py

# Arquivo para salvar os projetos que estamos monitorando
TRACKING_FILE = "legislativo_watchlist.json"

def load_watchlist() -> Dict:
    try:
        with open(TRACKING_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_watchlist(data: Dict):
    with open(TRACKING_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

# --- FUNÇÃO PARA ADICIONAR PROJETO AO MONITORAMENTO ---
def toggle_tracking(item_data: Dict) -> str:
    """Adiciona ou remove um item da lista de monitoramento."""
    watchlist = load_watchlist()
    uid = item_data.get('uid')
    
    if uid in watchlist:
        del watchlist[uid]
        save_watchlist(watchlist)
        return "removido"
    else:
        # Salva apenas o essencial para consultar depois
        watchlist[uid] = {
            "casa": item_data.get('casa'),
            "id_api": item_data.get('uid').split('_')[1], # Remove o prefixo CAM_ ou SEN_
            "sigla": item_data.get('tipo'),
            "numero": item_data.get('numero'),
            "ano": item_data.get('ano'),
            "ementa": item_data.get('ementa'),
            "link": item_data.get('link'),
            "last_status": "Monitoramento Iniciado"
        }
        save_watchlist(watchlist)
        return "adicionado"

# --- CONSULTA DE TRAMITAÇÕES (NOVO CORE) ---
async def check_tramitacoes_watchlist() -> List[Dict]:
    watchlist = load_watchlist()
    updates = []
    
    async with httpx.AsyncClient(timeout=10) as client:
        for uid, info in watchlist.items():
            try:
                novo_status = None
                
                # 1. Consulta CÂMARA
                if info['casa'] == 'Câmara':
                    url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{info['id_api']}/tramitacoes"
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        dados = resp.json().get('dados', [])
                        if dados:
                            # Pega a última tramitação
                            last = dados[-1]
                            novo_status = f"{last.get('dataHora', '')[:10]}: {last.get('despacho') or last.get('descricaoTramitacao')}"

                # 2. Consulta SENADO
                elif info['casa'] == 'Senado':
                    url = f"https://legis.senado.leg.br/dadosabertos/materia/movimentacoes/{info['id_api']}"
                    headers = {"Accept": "application/json"}
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        # Navegação no JSON complexo do Senado
                        movs = data.get('MovimentacaoMateria', {}).get('Materia', {}).get('Tramitacoes', {}).get('Tramitacao', [])
                        if isinstance(movs, dict): movs = [movs] # Normaliza se for item único
                        
                        if movs:
                            last = movs[0] # Senado costuma mandar o mais recente primeiro ou ultimo, verificar ordem
                            # No Senado, geralmente o array vem ordenado. Pegamos o mais recente.
                            # Mas garantimos ordenação por data se necessário.
                            desc = last.get('IdentificacaoTramitacao', {}).get('DescricaoSituacao') or last.get('TextoTramitacao')
                            data_mov = last.get('DataTramitacao', '')
                            novo_status = f"{data_mov}: {desc}"

                # Lógica de Atualização
                if novo_status and novo_status != info.get('last_status'):
                    # Houve mudança!
                    info['last_status'] = novo_status
                    updates.append({
                        "uid": uid,
                        "titulo": f"{info['sigla']} {info['numero']}/{info['ano']}",
                        "status": novo_status,
                        "link": info['link'],
                        "ementa": info['ementa']
                    })
            
            except Exception as e:
                print(f"Erro ao verificar {uid}: {e}")
                continue
    
    # Salva os novos status no arquivo para não alertar repetido
    if updates:
        save_watchlist(watchlist)
        
    return updates


# Adicione ao final do check_legislativo.py, ou junto das outras funções de check

async def find_proposition(casa: str, sigla: str, numero: str, ano: str) -> Dict:
    """Busca uma proposição específica para obter seus metadados e ID."""
    
    async with httpx.AsyncClient(timeout=15) as client:
        
        # --- BUSCA NA CÂMARA ---
        if casa == 'Câmara':
            url = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
            params = {
                "siglaTipo": sigla.strip().upper(),
                "numero": numero.strip(),
                "ano": ano.strip(),
                "ordem": "DESC",
                "ordenarPor": "id"
            }
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    dados = resp.json().get('dados', [])
                    if dados:
                        # Retorna o primeiro match
                        item = dados[0]
                        return {
                            "uid": f"CAM_{item['id']}",
                            "casa": "Câmara",
                            "tipo": item['siglaTipo'],
                            "numero": str(item['numero']),
                            "ano": str(item['ano']),
                            "ementa": item['ementa'],
                            "link": f"https://www.camara.leg.br/propostas-legislativas/{item['id']}",
                            "last_status": "Adicionado Manualmente"
                        }
            except Exception as e:
                print(f"Erro busca manual Câmara: {e}")

        # --- BUSCA NO SENADO ---
        elif casa == 'Senado':
            url = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"
            headers = {"Accept": "application/json"}
            params = {
                "sigla": sigla.strip().upper(),
                "numero": numero.strip(),
                "ano": ano.strip()
            }
            try:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    lista = data.get('PesquisaBasicaMateria', {}).get('Materias', {}).get('Materia', [])
                    if isinstance(lista, dict): lista = [lista]
                    
                    if lista:
                        # Pega o primeiro
                        dados = lista[0].get('DadosBasicosMateria', {})
                        cod = dados.get('CodigoMateria')
                        if cod:
                            return {
                                "uid": f"SEN_{cod}",
                                "casa": "Senado",
                                "tipo": dados.get('SiglaMateria'),
                                "numero": str(dados.get('NumeroMateria')),
                                "ano": str(dados.get('AnoMateria')),
                                "ementa": dados.get('EmentaMateria'),
                                "link": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{cod}",
                                "last_status": "Adicionado Manualmente"
                            }
            except Exception as e:
                print(f"Erro busca manual Senado: {e}")

    return None # Não encontrou
