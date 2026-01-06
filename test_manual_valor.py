# Nome do arquivo: test_manual_valor.py

import asyncio
import os
from datetime import datetime

# Importa a função principal do google_search.py (v14.0.5)
try:
    from google_search import perform_google_search
except ImportError:
    print("ERRO: Não encontrei o arquivo 'google_search.py'.")
    exit()
except Exception as e:
    print(f"ERRO ao importar 'google_search': {e}")
    exit()

# --- Configuração do Teste ---

# 1. Defina a data que você quer testar (YYYY-MM-DD)
#    (Vamos usar hoje, 10 de Novembro, para o teste)
DATA_TESTE = "2025-11-10"

# 2. Defina uma query de busca (exatamente como no config.json)
QUERY_TESTE = '"contas publicas" OR "politica fiscal" OR "Arcabouço fiscal"'

# -------------------------------

async def run_test():
    print("--- Iniciando Teste Manual do 'google_search.py' (v14.0.5) ---")
    
    # Verifica se as variáveis de ambiente estão carregadas
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx_id = os.environ.get("GOOGLE_CX_ID")
    
    if not api_key:
        print("ALERTA: Variável de ambiente 'GOOGLE_API_KEY' não encontrada.")
    if not cx_id:
        print("ALERTA: Variável de ambiente 'GOOGLE_CX_ID' não encontrada.")
    
    print(f"\nBuscando pela data: {DATA_TESTE}")
    print(f"Usando a query: {QUERY_TESTE}")
    
    try:
        # Chama a função que queremos testar
        results = await perform_google_search(
            query=QUERY_TESTE,
            search_date=DATA_TESTE
        )
        
        print("\n--- RESULTADO ---")
        if not results:
            print("Nenhum resultado encontrado.")
            print("\nPossíveis causas:")
            print(" 1. Chaves (API_KEY/CX_ID) incorretas ou não carregadas.")
            print(" 2. Configuração do Google CSE (*.valor.globo.com/*) está faltando.")
            print(" 3. O Google não indexou/encontrou notícias para esta query nesta data.")
            print(" 4. A API do Google bloqueou (ex: cota excedida).")
            return

        print(f"Sucesso! Encontrados {len(results)} resultados:")
        for i, res in enumerate(results):
            print(f"\nResultado #{i+1}:")
            print(f"  Título: {res.title}")
            print(f"  Link: {res.link}")
            print(f"  Snippet: {res.snippet[:100]}...")
            
    except Exception as e:
        print(f"\n--- ERRO CRÍTICO DURANTE O TESTE ---")
        print(f"Exceção: {e}")

if __name__ == "__main__":
    # Garante que as variáveis de ambiente (do seu .env, por ex) sejam carregadas
    # Se o senhor usa 'dotenv', descomente a linha abaixo:
    # from dotenv import load_dotenv
    # load_dotenv() 
    
    asyncio.run(run_test())
