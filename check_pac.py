# Nome do arquivo: check_pac.py
#
# Módulo para monitorar dotações do Novo PAC
# e alertar sobre mudanças.
#
# Versão 2.1:
# - Envia relatório diário completo (Dotação + Empenhado).
# - Destaca mudanças em relação ao dia anterior.
# - Gera cache histórico (2010-2025) para o dashboard.

import json
import os
import asyncio
from typing import Dict, Set, List, Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo # Para o fuso-horário

# Importa o sender do Telegram
from telegram import send_telegram_message

# Importa a biblioteca do SIOP
try:
    from orcamentobr import despesa_detalhada
except ImportError:
    print("ERRO: Biblioteca 'orcamentobr' não encontrada.")
    print("Por favor, adicione 'orcamentobr' ao seu requirements.txt")
    raise

# --- 1. Mapeamento dos Programas e Ações ---
PROGRAMAS_ACOES = {
    'PROSUB': {
        '123G': 'IMPLANTACAO DE ESTALEIRO E BASE NAVAL',
        '123H': 'CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR',
        '123I': 'CONSTRUCAO DE SUBMARINOS CONVENCIONAIS'
    },
    'PNM': {
        '14T7': 'DESENVOLVIMENTO DE TECNOLOGIA NUCLEAR'
    },
    'PRONAPA': {
        '1N47': 'CONSTRUCAO DE NAVIOS-PATRULHA 500T'
    }
}

# --- 2. Configuração do Estado ---
STATE_FILE_PATH = os.environ.get("PAC_STATE_FILE_PATH", "pac_state.json")
HISTORICAL_CACHE_PATH = os.environ.get("PAC_HISTORICAL_CACHE_PATH", "pac_historical_dotacao.json")


def load_pac_state() -> Dict[str, Dict[str, Dict[str, float]]]:
    """
    Carrega o estado (últimos valores por ano).
    Formato: { "2024": {"123G": {"dotacao": 100.0, "empenhado": 50.0}, ...} }
    """
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Validação simples da estrutura
            if not isinstance(data, dict):
                return {}
            
            final_state = {}
            for ano, acoes in data.items():
                if not isinstance(acoes, dict):
                    continue
                final_state[ano] = {}
                for acao_cod, values in acoes.items():
                    # Garante que a estrutura interna está correta
                    if isinstance(values, dict) and "dotacao" in values and "empenhado" in values:
                        final_state[ano][acao_cod] = {
                            "dotacao": float(values.get("dotacao", 0.0)),
                            "empenhado": float(values.get("empenhado", 0.0))
                        }
            return final_state
            
    except (FileNotFoundError, json.JSONDecodeError):
        return {} # Retorna um dict vazio se não existir ou for inválido

def save_pac_state(state: Dict[str, Dict[str, Dict[str, float]]]):
    """Salva o estado atual (valores por ano)."""
    try:
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Erro Crítico: Falha ao salvar estado do PAC: {e}")

def save_pac_historical_cache(data: Dict[str, Any]):
    """Salva o cache de dados históricos (para o gráfico)."""
    try:
        with open(HISTORICAL_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Erro Crítico: Falha ao salvar cache histórico do PAC: {e}")


# --- 3. Função de Busca de Dados ---
async def buscar_dados_acao_pac(ano: int, acao_cod: str) -> Optional[Dict[str, Any]]:
    """
    Busca os dados de UMA ação, totalizados.
    Retorna uma "linha" (dict) com os totais.
    """
    print(f"[PAC] Buscando dados totais para {ano}, Ação {acao_cod}...")
    try:
        # 1. Busca os dados detalhados (pode retornar múltiplas linhas)
        df_detalhado = despesa_detalhada(
            exercicio=ano,
            acao=acao_cod, # Filtro da ação
            inclui_descricoes=True,
            ignore_secure_certificate=True
        )
        
        if df_detalhado.empty:
            print(f"[PAC] Nenhum dado encontrado para {acao_cod} em {ano}.")
            return None
            
        # 2. Soma os valores para obter o TOTAL da ação
        colunas_numericas = ['loa', 'loa_mais_credito', 'empenhado', 'liquidado', 'pago']
        colunas_para_somar = [col for col in colunas_numericas if col in df_detalhado.columns]
        
        if not colunas_para_somar:
            return None
            
        # .sum() cria uma "Series" (basicamente uma linha de totais)
        totais_acao = df_detalhado[colunas_para_somar].sum()
        
        # Converte a "Series" do Pandas para um dicionário Python
        return totais_acao.to_dict()

    except Exception as e:
        print(f"Erro ao consultar o SIOP (PAC) para a ação {acao_cod}: {e}")
        # Envia um alerta de falha na busca
        await send_telegram_message(f"⚠️ Erro no Robô PAC:\nFalha ao consultar SIOP para Ação {acao_cod} (Ano {ano}).\nErro: {e}")
        return None

# --- Nova Função de Cache Histórico ---
async def update_pac_historical_cache():
    """
    Gera o cache de dotações (LOA + Créditos) de 2010 a 2025.
    Executa uma vez por dia, chamado pelo check_and_process_pac.
    """
    print("[PAC Cache Histórico] Iniciando geração do cache de 2010-2025...")
    
    YEAR_START = 2010
    YEAR_END = 2025 # Garante que o ano atual esteja incluído

    # Estrutura de dados otimizada para Chart.js
    labels = list(range(YEAR_START, YEAR_END + 1))
    datasets_map: Dict[str, Dict[str, Any]] = {}
    
    # Prepara os datasets
    for programa, acoes in PROGRAMAS_ACOES.items():
        for acao_cod, acao_desc in acoes.items():
            label_completo = f"{acao_cod} - {acao_desc.upper()}"
            datasets_map[acao_cod] = {
                "label": label_completo,
                "data": [0.0] * len(labels) # Inicializa todos os anos com 0.0
            }

    # Loop principal (16 anos * 5 ações = 80 queries)
    for i, ano in enumerate(labels):
        print(f"[PAC Cache Histórico] Processando ano: {ano}...")
        for acao_cod in datasets_map.keys():
            try:
                dados_linha = await buscar_dados_acao_pac(ano, acao_cod)
                await asyncio.sleep(0.5) # Pausa leve para não sobrecarregar API
                
                if dados_linha:
                    dotacao_atual = float(dados_linha.get('loa_mais_credito', 0.0))
                    # Atualiza o valor para este ano no índice correto
                    datasets_map[acao_cod]["data"][i] = dotacao_atual
            except Exception as e:
                print(f"[PAC Cache Histórico] Erro ao buscar {acao_cod} para {ano}: {e}")
                # Mantém 0.0 se falhar

    # Formato final do JSON
    chart_data = {
        "labels": labels,
        "datasets": list(datasets_map.values())
    }
    
    save_pac_historical_cache(chart_data)
    print("[PAC Cache Histórico] Cache histórico salvo com sucesso.")


# --- 4. Função Auxiliar de Formatação ---
def formatar_moeda(valor):
    """Formata um número como R$ 1.234,56"""
    if valor is None:
        return "R$ 0,00"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --- 5. Função Principal de Verificação ---
async def check_and_process_pac(ano_exercicio: str):
    """
    Função chamada pelo AGENDADOR (run_check.py).
    Envia relatório diário de Dotação/Empenhado e destaca mudanças.
    """
    print(f"--- Iniciando Relatório Diário do PAC (Dotações) para o ano: {ano_exercicio} ---")
    
    # 1. Carrega o estado (valores antigos)
    full_state = load_pac_state()
    previous_values_map = full_state.get(ano_exercicio, {})
    
    # 2. Busca os valores atuais
    current_values_map: Dict[str, Dict[str, float]] = {}
    
    try:
        ano_int = int(ano_exercicio)
    except ValueError:
        print(f"Ano inválido para o PAC: {ano_exercicio}")
        return

    # Primeiro, busca todos os dados atuais
    for programa, acoes in PROGRAMAS_ACOES.items():
        for acao_cod in acoes.keys():
            dados_linha = await buscar_dados_acao_pac(ano_int, acao_cod)
            await asyncio.sleep(1) # Pausa para não sobrecarregar a API
            
            if dados_linha is None:
                # Falha na busca, erro já enviado. Salva zero para não bugar o estado.
                dotacao_atual = 0.0
                empenhado_atual = 0.0
            else:
                dotacao_atual = float(dados_linha.get('loa_mais_credito', 0.0))
                empenhado_atual = float(dados_linha.get('empenhado', 0.0))

            current_values_map[acao_cod] = {
                "dotacao": dotacao_atual,
                "empenhado": empenhado_atual
            }

    # 3. Monta o relatório comparando com o dia anterior
    report_lines: List[str] = []
    changes_found: bool = False
    
    for programa, acoes in PROGRAMAS_ACOES.items():
        report_lines.append(f"\n*{programa}*") # Título do Programa
        
        for acao_cod, acao_desc in acoes.items():
            
            # Pega dados atuais (que acabamos de buscar)
            current_data = current_values_map.get(acao_cod, {"dotacao": 0.0, "empenhado": 0.0})
            dotacao_atual = current_data["dotacao"]
            empenhado_atual = current_data["empenhado"]

            # Pega dados antigos (do state salvo)
            previous_data = previous_values_map.get(acao_cod, {"dotacao": 0.0, "empenhado": 0.0})
            dotacao_antiga = previous_data["dotacao"]
            empenhado_antigo = previous_data["empenhado"]

            # Verifica mudanças
            dotacao_mudou = dotacao_atual != dotacao_antiga
            empenhado_mudou = empenhado_atual != empenhado_antigo
            
            if dotacao_mudou or empenhado_mudou:
                changes_found = True

            # Monta as linhas de texto para esta ação
            report_lines.append(f"*{acao_cod} - {acao_desc.upper()}*")
            
            # Linha da Dotação
            linha_dot = f"  Dotação: *{formatar_moeda(dotacao_atual)}*"
            if dotacao_mudou:
                linha_dot += f" (Ant: {formatar_moeda(dotacao_antiga)}) 🔺"
            report_lines.append(linha_dot)

            # Linha do Empenhado
            linha_emp = f"  Empenhado: *{formatar_moeda(empenhado_atual)}*"
            if empenhado_mudou:
                linha_emp += f" (Ant: {formatar_moeda(empenhado_antigo)}) 🔺"
            report_lines.append(linha_emp)
            report_lines.append("") # Linha em branco para espaçar

    # 4. Envia o relatório completo (TODOS OS DIAS)
    agora = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime('%d/%m/%Y às %H:%M')
    
    header = f"🗓️ *Relatório Diário de Dotações - Novo PAC* 🗓️\n(Exercício: {ano_exercicio} - Verificado em: {agora})\n"
    
    if changes_found:
        header += "\n🔔 *Mudanças detectadas desde ontem!* 🔔"
    else:
        header += "\n✅ *Sem mudanças desde ontem.* ✅"
    
    message = header + "\n" + "\n".join(report_lines)
    
    await send_telegram_message(message)
    print("Relatório diário do PAC enviado ao Telegram.")

    # 5. Salva o estado atual para a comparação de amanhã
    full_state[ano_exercicio] = current_values_map
    save_pac_state(full_state)

    # 6. Atualiza o cache histórico para o gráfico
    await update_pac_historical_cache()
    
    print(f"--- Verificação do PAC (Dotações) finalizada ---")
