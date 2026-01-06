# Nome do arquivo: dou_fallback.py
# Módulo de Redundância - Scraper do DOU Público (in.gov.br)
# Acionado quando o InLabs (XML) falha.

import httpx
from bs4 import BeautifulSoup
from datetime import datetime
import asyncio
import re
from typing import List, Dict

# URL de Busca Oficial do DOU
SEARCH_URL = "https://www.in.gov.br/consulta/-/buscar/dou"

async def buscar_dou_publico(termo: str, data_pt: str, secao: str = "do1") -> List[Dict]:
    """
    Busca um termo específico no site in.gov.br para uma data específica.
    data_pt: DD-MM-YYYY
    secao: do1, do2, do3
    """
    results = []
    
    # Parâmetros exatos que o site do DOU espera
    params = {
        "q": f'"{termo}"', # Aspas para busca exata
        "s": secao,
        "exact": "true",
        "dt": data_pt, # Data Inicio
        "dtEnd": data_pt, # Data Fim (mesmo dia)
        "sortType": "0"
    }
    
    # Headers para parecer um navegador real
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    print(f"[Fallback DOU] Buscando '{termo}' em {data_pt} ({secao})...")

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        try:
            resp = await client.get(SEARCH_URL, params=params, headers=headers)
            if resp.status_code != 200:
                print(f"[Fallback] Erro HTTP {resp.status_code} para '{termo}'")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Os scripts do Liferay (sistema do DOU) geram os resultados em cards
            # Procuramos os elementos da lista de resultados
            items = soup.find_all("div", class_="result-item") # Pode variar, mas geralmente é estruturado assim ou em scripts
            
            # Fallback de parsing: O site do DOU as vezes renderiza JSON dentro do HTML (Liferay)
            # Vamos buscar links diretos nos resultados renderizados (script-safe)
            script_results = soup.find_all("h5", class_="title-marker")

            for item in script_results:
                a_tag = item.find("a")
                if not a_tag: continue
                
                link_rel = a_tag.get("href")
                full_link = f"https://www.in.gov.br{link_rel}"
                title = a_tag.get_text(strip=True)
                
                # Pega o resumo/snippet se existir
                abstract = ""
                parent = item.find_parent("div")
                if parent:
                    p_tag = parent.find("p", class_="abstract-marker")
                    if p_tag: abstract = p_tag.get_text(strip=True)

                results.append({
                    "organ": "DOU Público (Fallback)", # Não temos o órgão fácil na lista, pegamos no clique se precisar
                    "type": "Resultado de Busca",
                    "summary": title,
                    "raw": f"{title}\n{abstract}\nLink: {full_link}",
                    "relevance_reason": f"Encontrado via busca de redundância pelo termo: '{termo}'",
                    "section": secao.upper(),
                    "link": full_link
                })
                
        except Exception as e:
            print(f"[Fallback] Erro na busca de '{termo}': {e}")

    return results

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    """
    Orquestrador da Redundância.
    Converte a data e dispara buscas paralelas para todas as keywords críticas.
    """
    # Converte YYYY-MM-DD para DD-MM-YYYY
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y")
    except:
        return []

    # Lista de termos obrigatórios para garantir cobertura orçamentária e estratégica
    termos_criticos = [
        "Marinha do Brasil",
        "Comando da Marinha",
        "Orçamento Fiscal",
        "Crédito Suplementar",
        "Remanejamento",
        "Ministério da Defesa",
        "PROSUB",
        "Amazul",
        "52131": "Comando da Marinha",
        "52133": "Secretaria da Comissão Interministerial para os Recursos do Mar",
        "52232": "Caixa de Construções de Casas para o Pessoal da Marinha - CCCPM",
        "52233": "Amazônia Azul Tecnologias de Defesa S.A. - AMAZUL",
        "52931": "Fundo Naval",
        "52932": "Fundo de Desenvolvimento do Ensino Profissional Marítimo",
        "52000": "Ministério da Defesa",
        "ministério da defesa",
        "forças armadas",
        "autoridade marítima",
        "comando da marinha",
        "marinha do brasil",
        "fundo naval",
        "amazônia azul tecnologias de defesa",
        "caixa de construções de casas para o pessoal da marinha",
        "empresa gerencial de projetos navais",
        "fundos públicos",
        "fundo público",
        "Relatório de Avaliação de Receitas e Despesas Primárias",
        "RARDP",
        "programação orçamentária e financeira",
        "Decreto de Programação Orçamentária e Financeira",
        "DPOF",
        "fundo de desenvolvimento do ensino profissional marítimo",
        "programa nuclear brasileiro",
        "amazônia azul",
        "lei orçamentária anual",
        "Lei de Diretrizes Orçamentárias",
        "Lei Orçamentária",
        "Plano Plurianual",
        "programação orçamentária e financeira",
        "altera grupos de natureza de despesa",
        "limites de movimentação",
        "limites de pagamento",
        "fontes de recursos",
        "movimentação e empenho"
    ]
    
    # Junta com as keywords do usuário (remove duplicatas)
    lista_busca = list(set(termos_criticos + keywords))
    
    tasks = []
    for kw in lista_busca:
        tasks.append(buscar_dou_publico(kw, data_pt, "do1")) # Foco na Seção 1 (Atos Normativos/Orçamento)
    
    # Executa tudo em paralelo
    resultados_matrix = await asyncio.gather(*tasks)
    
    # Aplaina a lista de listas
    final_pubs = []
    seen_links = set()
    
    for lista in resultados_matrix:
        for item in lista:
            if item['link'] not in seen_links:
                final_pubs.append(item)
                seen_links.add(item['link'])
                
    return final_pubs
