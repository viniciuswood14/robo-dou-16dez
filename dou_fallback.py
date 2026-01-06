# Nome do arquivo: dou_fallback.py
# Módulo de Redundância - Scraper do DOU Público (in.gov.br)

import httpx
from bs4 import BeautifulSoup
from datetime import datetime
import asyncio
from typing import List, Dict

# URL de Busca Oficial do DOU
SEARCH_URL = "https://www.in.gov.br/consulta/-/buscar/dou"

async def buscar_dou_publico(termo: str, data_pt: str, secao: str = "do1") -> List[Dict]:
    """
    Busca um termo específico no site in.gov.br para uma data específica.
    """
    results = []
    
    # Parâmetros exatos que o site do DOU espera
    params = {
        "q": f'"{termo}"', # Aspas para busca exata
        "s": secao,
        "exact": "true",
        "dt": data_pt,
        "dtEnd": data_pt,
        "sortType": "0"
    }
    
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
            script_results = soup.find_all("h5", class_="title-marker")

            for item in script_results:
                a_tag = item.find("a")
                if not a_tag: continue
                
                link_rel = a_tag.get("href")
                full_link = f"https://www.in.gov.br{link_rel}"
                title = a_tag.get_text(strip=True)
                
                abstract = ""
                parent = item.find_parent("div")
                if parent:
                    p_tag = parent.find("p", class_="abstract-marker")
                    if p_tag: abstract = p_tag.get_text(strip=True)

                results.append({
                    "organ": "DOU Público (Fallback)",
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
    """
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y")
    except:
        return []

    # Lista corrigida (apenas strings)
    termos_criticos = [
        "Marinha do Brasil",
        "Comando da Marinha",
        "Orçamento Fiscal",
        "Crédito Suplementar",
        "Remanejamento",
        "Ministério da Defesa",
        "PROSUB",
        "Amazul",
        "Emgepron",
        "Nuclep",
        "52131", # Buscamos pelo código da UG direto
        "52133",
        "52232",
        "52233",
        "52931", # Fundo Naval
        "52932",
        "52000",
        "Autoridade Marítima",
        "Fundo Naval",
        "Programação Orçamentária e Financeira",
        "Limite de Pagamento"
    ]
    
    # Junta com as keywords do usuário e remove duplicatas
    lista_busca = list(set(termos_criticos + keywords))
    
    # Limita para não fazer 50 requisições simultâneas e ser bloqueado pelo firewall do governo
    # Vamos focar nas top 15 se a lista for muito grande
    if len(lista_busca) > 15:
        print(f"[Fallback] Limitando busca às 15 primeiras keywords de {len(lista_busca)}...")
        lista_busca = lista_busca[:15]

    tasks = []
    for kw in lista_busca:
        tasks.append(buscar_dou_publico(kw, data_pt, "do1"))
    
    resultados_matrix = await asyncio.gather(*tasks)
    
    final_pubs = []
    seen_links = set()
    
    for lista in resultados_matrix:
        for item in lista:
            if item['link'] not in seen_links:
                final_pubs.append(item)
                seen_links.add(item['link'])
                
    return final_pubs
