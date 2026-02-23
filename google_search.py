# Nome do arquivo: google_search.py
# Versão: 14.1 (Radar Aberto - Sem trava do Valor Econômico)

import httpx
import os
from typing import List, Dict, Optional

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GOOGLE_CX_ID = os.environ.get("GOOGLE_CX_ID")

SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

class SearchResult(dict):
    """Helper para facilitar acesso aos campos do resultado"""
    @property
    def title(self) -> str:
        return self.get("title", "")
    
    @property
    def link(self) -> str:
        return self.get("link", "")

    @property
    def snippet(self) -> str:
        return self.get("snippet", "").replace("\n", " ")

async def perform_google_search(query: str, search_date: str) -> List[SearchResult]:
    """
    Busca no Google CSE por uma query, filtrando por data EXATA.
    
    :param query: Termos de busca
    :param search_date: Data no formato YYYY-MM-DD
    """
    if not GOOGLE_API_KEY or not GOOGLE_CX_ID:
        print("Erro: GOOGLE_API_KEY ou GOOGLE_CX_ID não configurados.")
        return []

    # A API do Google usa 'sort=date:r:YYYYMMDD:YYYYMMDD' para um dia específico
    try:
        # Converte "YYYY-MM-DD" para "YYYYMMDD"
        date_yyyymmdd = search_date.replace("-", "")
        date_sort_param = f"date:r:{date_yyyymmdd}:{date_yyyymmdd}"
    except Exception as e:
        print(f"Data inválida fornecida para busca: {search_date}. Erro: {e}")
        return []

    # CORREÇÃO: Removemos a trava do "site:valor.globo.com"
    # Agora a busca é feita livremente com base nas palavras-chave do api.py
    full_query = query
    
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CX_ID,
        "q": full_query,
        "num": 10, # Limita a 10 resultados
        "sort": date_sort_param # Adiciona o filtro de data exata
    }
    
    results = []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(SEARCH_URL, params=params, timeout=20)
            
            if response.status_code != 200:
                print(f"Erro na API do Google: {response.status_code} - {response.text}")
                return []

            data = response.json()
            items = data.get("items", [])
            
            for item in items:
                results.append(SearchResult(item))
                
            return results
            
    except Exception as e:
        print(f"Exceção ao buscar no Google: {e}")
        return []
