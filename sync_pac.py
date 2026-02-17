# Nome do arquivo: sync_pac.py
# Versão: 2.0 (Tratamento Inteligente de Excel)

import os
import imaplib
import email
from email.header import decode_header
import pandas as pd
import io
from github import Github
import warnings

# Ignora avisos de formatação do Excel
warnings.simplefilter("ignore")

# --- CONFIGURAÇÕES (Via Variáveis de Ambiente) ---
EMAIL_USER = os.getenv("GMAIL_USER")
EMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD") 
EMAIL_SENDER_FILTER = os.getenv("PAC_EMAIL_SENDER") 
EMAIL_SUBJECT_FILTER = os.getenv("PAC_EMAIL_SUBJECT", "PAC") 

GITHUB_TOKEN = os.getenv("MY_GITHUB_TOKEN") 
REPO_NAME = "viniciuswood14/robo-dou-16dez" 
FILE_PATH_IN_REPO = "pac_data.csv" 

def connect_imap():
    print("📧 Conectando ao Gmail...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_USER, EMAIL_PASS)
    return mail

def get_latest_attachment():
    mail = connect_imap()
    mail.select("INBOX")
    
    criteria = []
    if EMAIL_SENDER_FILTER:
        criteria.append(f'FROM "{EMAIL_SENDER_FILTER}"')
    if EMAIL_SUBJECT_FILTER:
        criteria.append(f'SUBJECT "{EMAIL_SUBJECT_FILTER}"')
    
    search_query = f"({' '.join(criteria)})" if criteria else "ALL"
    print(f"🔎 Buscando e-mails: {search_query}")
    
    status, messages = mail.search(None, search_query)
    if status != "OK" or not messages[0]:
        print("⚠️ Nenhum e-mail encontrado.")
        return None

    latest_id = messages[0].split()[-1]
    status, msg_data = mail.fetch(latest_id, "(RFC822)")
    
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            msg = email.message_from_bytes(response_part[1])
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding if encoding else "utf-8")
            print(f"📩 Processando e-mail: {subject}")

            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get("Content-Disposition") is None:
                    continue

                filename = part.get_filename()
                if filename:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in [".xlsx", ".xls", ".csv"]:
                        print(f"📎 Anexo encontrado: {filename}")
                        return part.get_payload(decode=True), ext
    
    print("⚠️ Nenhum anexo compatível encontrado.")
    return None

def convert_to_csv(content, ext):
    print("🔄 Tratando planilha e convertendo para CSV...")
    
    try:
        # 1. Lê o arquivo bruto (sem header ainda)
        if ext in [".xlsx", ".xls"]:
            # Lê as primeiras 20 linhas para achar onde começa
            df_raw = pd.read_excel(io.BytesIO(content), header=None, nrows=30)
        else:
            df_raw = pd.read_csv(io.BytesIO(content), header=None, nrows=30, sep=None, engine='python')
        
        # 2. Descobre em qual linha está o cabeçalho real
        # Procura pela célula que contém "Mês Lançamento" ou "Ação Governo"
        header_idx = -1
        for idx, row in df_raw.iterrows():
            row_str = " ".join([str(x) for x in row.values]).lower()
            if "mês lançamento" in row_str or "ação governo" in row_str or "dotacao atualizada" in row_str:
                header_idx = idx
                break
        
        if header_idx == -1:
            print("❌ Erro: Não encontrei a linha de cabeçalho padrão na planilha.")
            return None

        print(f"✅ Cabeçalho detectado na linha {header_idx + 1}")

        # 3. Relê o arquivo usando a linha certa como header
        if ext in [".xlsx", ".xls"]:
            df = pd.read_excel(io.BytesIO(content), header=header_idx)
        else:
            df = pd.read_csv(io.BytesIO(content), header=header_idx, sep=None, engine='python')

        # 4. Limpeza e Seleção de Colunas
        # Normaliza nomes das colunas (remove espaços, uppercase) para achar fácil
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # Mapa de tradução: { "NOME NA PLANILHA": "NOME NO SISTEMA" }
        # Baseado no seu api.py e no arquivo enviado
        col_map = {
            "MÊS LANÇAMENTO": "MÊS",
            "AÇÃO GOVERNO": "AÇÃO",
            "DOTACAO ATUALIZADA": "ATUALIZADA",
            "PROVISAO CONCEDIDA": "PROVISAO",
            "DESTAQUE CONCEDIDO": "DESTAQUE",
            "DESPESAS EMPENHADAS": "EMPENHADA",
            "DESPESAS PAGAS": "PAGAS"
        }
        
        # Filtra só as colunas que existem e renomeia
        cols_to_keep = []
        for original, target in col_map.items():
            if original in df.columns:
                cols_to_keep.append(original)
        
        if not cols_to_keep:
            print("❌ Erro: Nenhuma coluna relevante encontrada.")
            return None
            
        df_final = df[cols_to_keep].rename(columns=col_map)
        
        # Remove linhas vazias (ex: totalizadores no final)
        df_final = df_final.dropna(subset=["AÇÃO"])
        
        # 5. Exporta para CSV limpo
        output = io.StringIO()
        df_final.to_csv(output, index=False, sep=";", encoding="utf-8-sig")
        
        print(f"✅ Conversão concluída: {len(df_final)} registros processados.")
        return output.getvalue()
        
    except Exception as e:
        print(f"❌ Erro na conversão: {e}")
        return None

def update_github_file(new_content):
    if not new_content: return False
    
    print("🐙 Conectando ao GitHub...")
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    
    try:
        contents = repo.get_contents(FILE_PATH_IN_REPO)
        old_content_decoded = contents.decoded_content.decode("utf-8-sig")
        
        if old_content_decoded == new_content:
            print("✅ O arquivo no GitHub já está idêntico. Nada a fazer.")
            return False
            
        repo.update_file(
            path=contents.path,
            message="🤖 Update Automático: PAC Data (Tratado)",
            content=new_content,
            sha=contents.sha,
            branch="main" 
        )
        print("🚀 Sucesso! Arquivo pac_data.csv atualizado no GitHub.")
        return True
    except Exception as e:
        print(f"❌ Erro ao atualizar GitHub: {e}")
        return False

def sync_pac_routine():
    print("--- Iniciando Sincronização PAC (Gmail -> GitHub) ---")
    try:
        result = get_latest_attachment()
        if not result:
            return
        
        content_bytes, ext = result
        csv_content = convert_to_csv(content_bytes, ext)
        
        updated = update_github_file(csv_content)
        if updated:
            print("🔄 O Render deve iniciar um deploy automático em breve.")
            
    except Exception as e:
        print(f"❌ Erro na rotina de sync: {e}")

if __name__ == "__main__":
    sync_pac_routine()
