# Nome do arquivo: sync_pac.py
# Versão: 2.2 (Fix: Acentos no Assunto/IMAP UTF-8)

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

# --- CONFIGURAÇÕES (Variáveis de Ambiente) ---
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
    
    # Filtros de busca
    criteria = []
    if EMAIL_SENDER_FILTER:
        criteria.append(f'FROM "{EMAIL_SENDER_FILTER}"')
    
    # IMPORTANTE: Se o assunto tiver acentos, o critério precisa ser montado com cuidado
    # A biblioteca imaplib + Gmail funciona melhor com charset UTF-8 explícito
    if EMAIL_SUBJECT_FILTER:
        criteria.append(f'SUBJECT "{EMAIL_SUBJECT_FILTER}"')
    
    search_query = f"({' '.join(criteria)})" if criteria else "ALL"
    print(f"🔎 Buscando e-mails: {search_query}")
    
    # --- CORREÇÃO AQUI ---
    # Mudamos de mail.search(None, ...) para mail.search("UTF-8", ...)
    # Isso permite buscar palavras como "Execução" sem dar erro de ASCII.
    try:
        status, messages = mail.search("UTF-8", search_query)
    except Exception as e:
        print(f"⚠️ Erro ao buscar (tentando fallback ASCII): {e}")
        # Fallback: Tenta buscar sem acentos se der erro muito grave
        status, messages = mail.search(None, "ALL")

    if status != "OK" or not messages[0]:
        print("⚠️ Nenhum e-mail encontrado.")
        return None

    # Pega o último ID (o mais recente)
    # messages[0] é uma string com IDs separados por espaço: "1 2 3 4"
    ids = messages[0].split()
    latest_id = ids[-1] # Pega o último
    
    status, msg_data = mail.fetch(latest_id, "(RFC822)")
    
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            msg = email.message_from_bytes(response_part[1])
            subject_val, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject_val, bytes):
                subject_val = subject_val.decode(encoding if encoding else "utf-8")
            print(f"📩 Processando e-mail: {subject_val}")

            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get("Content-Disposition") is None:
                    continue

                filename = part.get_filename()
                if filename:
                    # Decodifica nome do arquivo se vier codificado
                    fname_val, fname_enc = decode_header(filename)[0]
                    if isinstance(fname_val, bytes):
                        filename = fname_val.decode(fname_enc if fname_enc else "utf-8")

                    ext = os.path.splitext(filename)[1].lower()
                    if ext in [".xlsx", ".xls", ".csv"]:
                        print(f"📎 Anexo encontrado: {filename}")
                        return part.get_payload(decode=True), ext
    
    print("⚠️ Nenhum anexo (.xlsx/.csv) encontrado no e-mail mais recente.")
    return None

def convert_to_csv(content, ext):
    print("🔄 Tratando planilha e convertendo para CSV...")
    
    try:
        # 1. Lê o arquivo bruto
        if ext in [".xlsx", ".xls"]:
            df_raw = pd.read_excel(io.BytesIO(content), header=None, nrows=30)
        else:
            df_raw = pd.read_csv(io.BytesIO(content), header=None, nrows=30, sep=None, engine='python')
        
        # 2. Localiza o cabeçalho
        header_idx = -1
        for idx, row in df_raw.iterrows():
            row_str = " ".join([str(x) for x in row.values]).lower()
            if "mês lançamento" in row_str or "ação governo" in row_str:
                header_idx = idx
                break
        
        if header_idx == -1:
            print("❌ Erro: Não encontrei a linha de cabeçalho (Mês Lançamento).")
            return None

        print(f"✅ Cabeçalho detectado na linha {header_idx + 1}")

        # 3. Relê com header correto
        if ext in [".xlsx", ".xls"]:
            df = pd.read_excel(io.BytesIO(content), header=header_idx)
        else:
            df = pd.read_csv(io.BytesIO(content), header=header_idx, sep=None, engine='python')

        # 4. Normaliza colunas
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        col_map = {
            "MÊS LANÇAMENTO": "MÊS",
            "AÇÃO GOVERNO": "AÇÃO",
            "DOTACAO ATUALIZADA": "ATUALIZADA",
            "PROVISAO CONCEDIDA": "PROVISAO",
            "DESTAQUE CONCEDIDO": "DESTAQUE",
            "DESPESAS EMPENHADAS": "EMPENHADA",
            "DESPESAS PAGAS": "PAGAS"
        }
        
        cols_found = [c for c in col_map.keys() if c in df.columns]
        if not cols_found:
            print(f"❌ Erro: Colunas incompatíveis. Achei: {df.columns}")
            return None
            
        df_final = df[cols_found].rename(columns=col_map)
        df_final = df_final.dropna(subset=["AÇÃO"])

        # Trata números (remove R$, troca vírgula por ponto se necessário, mas aqui vamos deixar o pandas lidar)
        # O ideal para CSV de sistema é ter padrão US (ponto decimal) ou BR consistente.
        # Vamos forçar numérico onde der.
        num_cols = ["ATUALIZADA", "PROVISAO", "DESTAQUE", "EMPENHADA", "PAGAS"]
        for col in num_cols:
            if col in df_final.columns:
                 # Remove caracteres não numéricos se for string
                 if df_final[col].dtype == object:
                    df_final[col] = df_final[col].astype(str).str.replace('R$', '', regex=False).str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
                 df_final[col] = pd.to_numeric(df_final[col], errors='coerce').fillna(0)

        # 5. Exporta
        output = io.StringIO()
        df_final.to_csv(output, index=False, sep=";", encoding="utf-8-sig")
        
        print(f"✅ Conversão concluída: {len(df_final)} linhas.")
        return output.getvalue()
        
    except Exception as e:
        print(f"❌ Erro conversão: {e}")
        return None

def update_github_file(new_content):
    if not new_content: return False
    
    print("🐙 Conectando ao GitHub...")
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        contents = repo.get_contents(FILE_PATH_IN_REPO)
        
        old_content_decoded = contents.decoded_content.decode("utf-8-sig")
        
        # Compara removendo espaços em branco extras para evitar falso update
        if old_content_decoded.strip() == new_content.strip():
            print("✅ GitHub já atualizado.")
            return False
            
        repo.update_file(
            path=contents.path,
            message="🤖 Update Automático: PAC Data",
            content=new_content,
            sha=contents.sha,
            branch="main" 
        )
        print("🚀 Sucesso! GitHub atualizado.")
        return True
    except Exception as e:
        print(f"❌ Erro GitHub: {e}")
        return False

def sync_pac_routine():
    print("--- Iniciando Sync PAC ---")
    res = get_latest_attachment()
    if res:
        content, ext = res
        csv_data = convert_to_csv(content, ext)
        if csv_data:
            update_github_file(csv_data)

if __name__ == "__main__":
    sync_pac_routine()
