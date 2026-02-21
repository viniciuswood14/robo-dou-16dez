# Nome do arquivo: api.py
# Versão: 21.3 (Fix: Correção de Aspas e Limpeza)

from fastapi import FastAPI, Form, HTTPException, Path, Request
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles 
from pydantic import BaseModel
from typing import List, Optional, Set, Dict, Any
from datetime import datetime
import os, io, zipfile, json, re, csv
from urllib.parse import urljoin
import asyncio
import httpx
from bs4 import BeautifulSoup
from fastapi.responses import Response

# Carrega o arquivo de configuração globalmente
try:
    with open("config.json", "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    print("⚠️ Aviso: config.json não encontrado. O sistema tentará usar Variáveis de Ambiente.")
    CONFIG = {}
except Exception as e:
    print(f"⚠️ Erro ao ler config.json: {e}")
    CONFIG = {}

# IA / Gemini
import google.generativeai as genai

# Google Drive API & Auth
from googleapiclient.discovery import build
from google.oauth2 import service_account

# --- NOVO: Importação do Telegram para a API avisar também ---
try:
    from telegram import send_telegram_message
except ImportError:
    # Mock caso falhe a importação, para não quebrar o site
    async def send_telegram_message(msg):
        print(f"[API TELEGRAM MOCK] {msg}")
        return False

# Tenta importar módulos opcionais (Robôs específicos)
try:
    from google_search import perform_google_search, SearchResult
except ImportError:
    pass

try:
    from check_legislativo import (
        check_and_process_legislativo, 
        toggle_tracking, 
        load_watchlist, 
        check_tramitacoes_watchlist, 
        find_proposition
    )
except ImportError:
    pass

try:
    from dou_fallback import executar_fallback
except ImportError:
    executar_fallback = None

# =====================================================================================
# API SETUP
# =====================================================================================

app = FastAPI(title="Robô CORM API - v21.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURAÇÃO DE SENHA ---
# Defina SITE_PASSWORD no Render. Se não tiver, usa 'admin' (Cuidado!)
SITE_PASSWORD = os.getenv("SITE_PASSWORD", "admin")

# =====================================================================================
# MIDDLEWARE DE AUTENTICAÇÃO (O PORTEIRO)
# =====================================================================================

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    
    # 1. Lista de arquivos/rotas que NÃO precisam de senha (públicos)
    public_paths = ["/login", "/auth", "/favicon.ico", "/health"]
    public_extensions = (".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".json", ".ico")

    if path in public_paths or path.endswith(public_extensions):
        return await call_next(request)

    # 2. Verifica se tem o Cookie de Acesso
    auth_cookie = request.cookies.get("corm_session")
    
    # Se o cookie for válido (igual à senha atual), deixa passar
    if auth_cookie == SITE_PASSWORD:
        return await call_next(request)
    
    # 3. Se não tiver cookie e não for público, manda pro Login
    return RedirectResponse(url="/login", status_code=303)

# =====================================================================================
# ROTAS DE LOGIN
# =====================================================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    # Retorna o arquivo login.html que criamos na pasta static
    if os.path.exists("static/login.html"):
        return FileResponse("static/login.html")
    # Fallback simples caso o arquivo não exista
    return "<html><body style='font-family: sans-serif; display:flex; justify-content:center; align-items:center; height:100vh; background:#f0f2f5;'><div style='background:white; padding:2rem; border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,0.1); text-align:center;'><h2>Acesso Restrito</h2><form action='/login' method='post'><input type='password' name='senha' placeholder='Senha do Sistema' style='padding:10px; margin-bottom:10px; width:100%; box-sizing:border-box;' required/><br/><input type='submit' value='Entrar' style='padding:10px 20px; background:#002c5f; color:white; border:none; border-radius:4px; cursor:pointer;'/></form></div></body></html>"

@app.post("/login")
async def login_submit(senha: str = Form(...)):
    # Verifica a senha
    if senha == SITE_PASSWORD:
        # Senha correta: Cria o cookie e redireciona para a Home
        response = RedirectResponse(url="/", status_code=303)
        # O cookie dura até o navegador fechar (session)
        response.set_cookie(key="corm_session", value=SITE_PASSWORD, httponly=True)
        return response
    else:
        # Senha errada: Volta pro login
        return RedirectResponse(url="/login?erro=1", status_code=303)

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("corm_session")
    return response

def pick_pdf_link_from_listing(html: str, base_url_for_rel: str, section_key: str) -> Optional[str]:
    # Encontra o link do PDF (ex: DO1.pdf) dentro da listagem do InLabs.
    soup = BeautifulSoup(html, "html.parser")
    target = section_key.upper() # ex: "DO1"
    
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # Procura por .pdf e pelo nome da seção (DO1, DO2, DO3)
        if href.lower().endswith(".pdf"):
            if target in href.upper() or target in a.get_text().upper():
                return urljoin(base_url_for_rel, href)
    return None

# ==========================================
@app.get("/download-pdf-inlabs")
async def download_pdf_inlabs(date: str, section: str = "do1"):
    print(f">>> [PDF InLabs Force] Iniciando fluxo para: {date} ({section})")
    
    # 1. Configura Login
    if "CONFIG" not in globals():
        email = os.getenv("INLABS_EMAIL")
        senha = os.getenv("INLABS_PASSWORD")
    else:
        email = CONFIG.get("inlabs_email") or os.getenv("INLABS_EMAIL")
        senha = CONFIG.get("inlabs_password") or os.getenv("INLABS_PASSWORD")
    
    if not email or not senha:
        return Response(content="Credenciais InLabs não configuradas.", status_code=500)

    # 2. Prepara URLs
    try:
        dt_obj = datetime.strptime(date, "%Y-%m-%d")
        data_pt = dt_obj.strftime("%d-%m-%Y")
        param_p = dt_obj.strftime("%Y-%m-%d")
        
        sec_code = section.lower()
        filename = f"{dt_obj.strftime('%Y_%m_%d')}_ASSINADO_{sec_code}.pdf"
        
        url_leitura = f"https://www.in.gov.br/leiturajornal?data={data_pt}&secao={section}"
        url_download = f"https://inlabs.in.gov.br/index.php?p={param_p}&dl={filename}"
        
    except Exception as e:
        return Response(content=f"Erro data: {str(e)}", status_code=400)

    # 3. Headers Base (Chrome)
    headers_base = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Referer": "https://www.in.gov.br/",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Ch-Ua": '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document"
    }

    async with httpx.AsyncClient(timeout=90, follow_redirects=True, verify=False, headers=headers_base) as client:
        try:
            # PASSO A: Login no Portal Principal
            print(">>> [1/4] Logando...")
            await client.get("https://www.in.gov.br/acesso.do") 
            
            payload = {"j_username": email, "j_password": senha, "entrar": "Entrar"}
            login_resp = await client.post(
                "https://www.in.gov.br/login_p_p_id_58_INSTANCE_942k_", 
                data=payload
            )
            
            if "Falha ao entrar" in login_resp.text:
                 return Response(content="Falha no Login InLabs. Verifique senha.", status_code=401)

            # PASSO B: Validar Sessão na Leitura
            print(f">>> [2/4] Validando sessão em: {url_leitura}")
            await client.get(url_leitura)
            await asyncio.sleep(1)

            # --- PASSO C (NUCLEAR): Extração e Injeção Manual de Cookies ---
            print(">>> [3/4] Construindo Header de Cookie Unificado...")
            cookies_dict = {c.name: c.value for c in client.cookies.jar}
            cookie_header_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])
            
            download_headers = headers_base.copy()
            download_headers["Cookie"] = cookie_header_str
            download_headers["Host"] = "inlabs.in.gov.br"
            download_headers["Referer"] = url_leitura
            download_headers["Sec-Fetch-Site"] = "same-site"
            
            print(f">>> [4/4] Baixando PDF (Forced Cookies): {url_download}")
            
            request = client.build_request("GET", url_download, headers=download_headers)
            
            async with client.send(request, stream=True) as r_pdf:
                if r_pdf.status_code != 200:
                    return Response(content=f"Erro {r_pdf.status_code} ao acessar arquivo.", status_code=404)
                
                body_content = await r_pdf.aread()
                tamanho = len(body_content)
                print(f">>> Tamanho final: {tamanho} bytes")
                
                if tamanho < 20000: 
                    try:
                        texto = body_content.decode('utf-8', errors='ignore')
                        titulo = texto.split("<title>")[1].split("</title>")[0] if "<title>" in texto else "Sem Título"
                        print(f">>> CONTEÚDO ERRO: {titulo}")
                    except: pass

                    return Response(
                        content=f"ERRO: O servidor rejeitou a sessão (Arquivo: {tamanho}b - {titulo}).",
                        status_code=502
                    )

                return Response(
                    content=body_content,
                    media_type="application/pdf",
                    headers={
                        "Content-Disposition": f"attachment; filename={filename}",
                        "Content-Length": str(tamanho)
                    }
                )

        except Exception as e:
            print(f"Erro Crítico: {e}")
            return Response(content=f"Erro interno no fluxo: {str(e)}", status_code=500)

# =====================================================================================
# INICIALIZAÇÃO E CONFIGURAÇÕES
# =====================================================================================

@app.on_event("startup")
async def startup_event():
    print(">>> SISTEMA UNIFICADO INICIADO (v21.3 - Secure + Drive + Telegram + Fix) <<<")
    
    # Validação PAC
    try:
        data = await load_pac_data_source()
        parsed = parse_pac_csv(data)
        count = sum(len(v) for v in parsed.values())
        print(f">>> Sucesso! CSV PAC carregado: {len(parsed)} anos, {count} registros processados.")
    except Exception as e:
        print(f">>> ALERTA: Erro ao ler CSV do PAC: {e}")

    # Inicializa Loops de Verificação
    try:
        from run_check import main_loop
        asyncio.create_task(main_loop())
    except ImportError:
        pass

try:
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

INLABS_BASE = os.getenv("INLABS_BASE", config.get("INLABS_BASE", "https://inlabs.in.gov.br"))
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
INLABS_USER = os.getenv("INLABS_USER", config.get("INLABS_USER", None))
INLABS_PASS = os.getenv("INLABS_PASS", config.get("INLABS_PASS", None))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", config.get("GEMINI_API_KEY", None))

# Configuração Fonte PAC
PAC_DATA_FILE = "pac_data.csv" 
PAC_DATA_URL = os.getenv("PAC_DATA_URL", None)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Configurações de TAGS e Prompts
MPO_NAVY_TAGS = config.get("MPO_NAVY_TAGS", {})
KEYWORDS_DIRECT_INTEREST_S1 = config.get("KEYWORDS_DIRECT_INTEREST_S1", [])
BUDGET_KEYWORDS_S1 = config.get("BUDGET_KEYWORDS_S1", [])
MPO_ORG_STRING = config.get("MPO_ORG_STRING", "ministério do planejamento e orçamento")
PERSONNEL_ACTION_VERBS = config.get("PERSONNEL_ACTION_VERBS", [])
TERMS_AND_ACRONYMS_S2 = config.get("TERMS_AND_ACRONYMS_S2", [])
NAMES_TO_TRACK = sorted(list(set(config.get("NAMES_TO_TRACK", []))), key=str.lower)

ANNOTATION_POSITIVE_GENERIC = config.get("ANNOTATION_POSITIVE_GENERIC", "")
ANNOTATION_NEGATIVE = config.get("ANNOTATION_NEGATIVE", "")

GEMINI_MASTER_PROMPT = """
Você é um analista de inteligência e orçamento do Comando da Marinha do Brasil.
Sua tarefa é ler a publicação do DOU e escrever UMA frase curta para relatório de WhatsApp. O foco é identificar se há impacto, benefício, restrição ou relevância transversal para o Ministério da Defesa ou Marinha.

REGRAS DE OURO (PUNIÇÃO SE VIOLADAS):
1. ZERO CONVERSA: NUNCA use jargões como "Após análise detalhada", "O documento trata de", "Informo que não foram encontradas portarias do MPO", etc. Vá direto ao assunto.
2. NÃO JUSTIFIQUE: NUNCA justifique sua resposta afirmando que um órgão X não é o órgão Y. Apenas resuma o fato útil.
3. DIRETO AO PONTO: Exemplo de saída perfeita: "Consolida entendimentos jurídicos (Súmulas) sobre temas como pensões militares e processos tributários."
4. FILTRO DE LIXO: Se a matéria for puramente administrativa interna de outro ministério e não tiver NENHUMA utilidade ou impacto para a Defesa, escreva APENAS a frase: "Sem impacto direto."
"""

GEMINI_MPO_PROMPT = """
### ROLE
Você é um Especialista em Análise Orçamentária e Defesa (Marinha do Brasil). Sua função é ler atos normativos de Órgãos Centrais (MPO, MF, MGI, Casa Civil) e extrair dados cruciais para a Defesa.

### REGRAS DE OURO (COMPORTAMENTO ESTRITO)
1. ZERO CONVERSA: NUNCA use frases explicativas ("Com base na análise...", "Trata-se de...", "Não foram encontradas...", "Classifico como..."). NUNCA crie "Notas do Especialista".
2. ZERO METADADOS: NUNCA escreva cabeçalhos de sistema ("TIPO 1", "TIPO 5"). NUNCA repita o nome/número da portaria na sua resposta, o sistema do robô já faz isso automaticamente.
3. ENTREGUE APENAS O TEXTO FINAL FORMATADO.

### DIRETRIZES DE BUSCA (UOs da MB/MD)
Busque impactos financeiros DIREtos (valores em R$) para:
- 52131 (Comando da Marinha), 52133 (SECIRM), 52232 (CCCPM), 52233 (AMAZUL), 52931 (Fundo Naval), 52932 (Ensino Profissional) e 52000 (MD).

### ESTRUTURA DE RESPOSTA OBRIGATÓRIA
Selecione APENAS UM dos formatos abaixo e imprima SOMENTE ELE (sem adicionar mais nenhuma palavra):

Se for Crédito Suplementar COM Impacto MB/MD (cite os valores da Marinha):
⚓ MB:
✅Suplementações (Total) – R$ [Valor]
[Ação]: R$ [Valor]
✅Cancelamentos (Total) – R$ [Valor]
[Ação]: R$ [Valor]

Se for Movimentação e Empenho COM Impacto MB/MD (Limites):
⚓ MD:
✅Ampliação do Limite de Movimentação e Empenho:
RP2: R$ [Valor] / RP3: R$ [Valor]

Se for GND ou Fonte COM Impacto MB/MD:
⚓ Alteração [GND ou Fonte]: [Descrição] - R$ [Valor]

Se o ato NÃO citar as UOs acima ou tratar apenas de diretrizes transversais genéricas (mesmo sendo do MGI/MPO/MF):
⚓ MB: Para conhecimento. Sem impacto para a Marinha. [Apenas se o ato trouxer alguma regra administrativa transversal, adicione 1 frase curta colada resumindo. Ex: "A portaria trata de regras sobre consignações em folha de pagamento". Se for apenas crédito/orçamento para outros ministérios da saúde/educação, feche o texto na palavra "Marinha." sem resumir].
"""
GEMINI_PESSOAL_PROMPT = """
Você é um assistente de inteligência da Marinha do Brasil analisando publicações de pessoal (Seção 2 do DOU).
Sua única missão é: ler a publicação e resumir em UMA frase curta (máximo 2 linhas) o que aconteceu EXCLUSIVAMENTE com o nosso "Alvo Identificado" (ex: para qual cargo, missão, tarefa ou ato ele foi designado/afetado).

REGRA DE OURO: IGNORE completamente outras pessoas, autoridades, civis ou militares de outras forças (como o Exército) que possam aparecer na mesma portaria. Se o nome não for o nosso alvo, não o mencione na sua resposta.

Exemplo de saída: "O [Posto] [NOME DO ALVO] foi designado(a) para [Ação/Tarefa]."
NUNCA responda "Sem impacto". A ação da pessoa alvo deve ser descrita.
"""

GEMINI_VALOR_PROMPT = "Analista financeiro da Marinha. Resumo de 1 frase sobre impacto para Defesa/Orçamento."

# --- Configuração Google Drive Auth ---
SCOPES_DRIVE = ['https://www.googleapis.com/auth/drive.readonly']

def get_drive_service():
    # Tenta autenticar no Google Drive usando Variável de Ambiente ou JSON local.
    creds = None
    
    # 1. Tenta ler da Variável de Ambiente (String JSON)
    json_env = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_env:
        try:
            info = json.loads(json_env)
            creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES_DRIVE)
        except json.JSONDecodeError:
            print("❌ Erro: A variável GOOGLE_SERVICE_ACCOUNT_JSON não é um JSON válido.")
    
    # 2. Se não deu certo, tenta arquivo local
    if not creds and os.path.exists("service_account.json"):
        creds = service_account.Credentials.from_service_account_file("service_account.json", scopes=SCOPES_DRIVE)
        
    if creds:
        return build('drive', 'v3', credentials=creds)
    else:
        print("⚠️ Aviso: Credenciais do Google Drive não encontradas.")
        return None

# =====================================================================================
# CLASSES E MODELOS
# =====================================================================================

class Publicacao(BaseModel):
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    raw: Optional[str] = None
    relevance_reason: Optional[str] = None
    section: Optional[str] = None
    clean_text: Optional[str] = None
    is_mpo_navy_hit: bool = False
    is_parsed_mpo: bool = False

class ProcessResponse(BaseModel):
    date: str
    count: int
    publications: List[Publicacao]
    whatsapp_text: str

class ValorPublicacao(BaseModel):
    titulo: str
    link: str
    analise_ia: str

class ProcessResponseValor(BaseModel):
    date: str
    count: int
    publications: List[ValorPublicacao]
    whatsapp_text: str

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s: return ""
    return _ws.sub(" ", s).strip()

def clean_title(raw_title: str) -> str:
    t = raw_title
    t = t.replace("nA", "Nº").replace("na", "Nº")
    t = t.replace(".2025", "/2025").replace(".2024", "/2024")
    t = t.replace("GM.MPO", "GM/MPO").replace("e an", "")
    t = t.replace("_", " ").replace(".doc", "").replace(".xml", "")
    t = re.sub(r"-\d+$", "", t)
    return norm(t)

def clean_html_text(raw_text: str) -> str:
    if not raw_text or "<" not in raw_text:
        return raw_text
    try:
        soup = BeautifulSoup(raw_text, "html.parser")
        return soup.get_text(" ", strip=True)
    except:
        return raw_text

def extract_fallback_summary(text: str) -> str:
    clean_txt = clean_html_text(text)
    match = re.search(r"(Abre aos? Orçamentos?.*?vigente\.?)", clean_txt, re.IGNORECASE | re.DOTALL)
    if match: return norm(match.group(1))
    match2 = re.search(r"(Altera.*?providências\.?)", clean_txt, re.IGNORECASE | re.DOTALL)
    if match2: return norm(match2.group(1))
    match3 = re.search(r"^.*?(?:RESOLVE:?|DECIDE:?)(.*)", clean_txt, re.DOTALL | re.IGNORECASE)
    if match3:
        candidate = match3.group(1).strip()
        return candidate[:300] + ("..." if len(candidate) > 300 else "")
    return clean_txt[:350] + ("..." if len(clean_txt) > 350 else "")

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    meses_pt = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}
    try:
        dt = datetime.fromisoformat(when)
        dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except: dd = when

    lines = [f"Bom dia, senhores!", "", f"PTC as seguintes publicações de interesse no DOU de {dd}:", ""]

    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —")
        return "\n".join(lines)

    pubs_by_section: Dict[str, List[Publicacao]] = {}
    for p in pubs:
        sec = p.section or "DOU"
        if "DO1" in sec or "Seção 1" in sec: sec_key = "1_DO1"
        elif "DO2" in sec or "Seção 2" in sec: sec_key = "2_DO2"
        elif "DO3" in sec or "Seção 3" in sec: sec_key = "3_DO3"
        else: sec_key = "4_OUTROS"
        pubs_by_section.setdefault(sec_key, []).append(p)

    for sec_key in sorted(pubs_by_section.keys()):
        label = "🔰 Seção 1" if "DO1" in sec_key else ("🔰 Seção 2" if "DO2" in sec_key else "🔰 Outros")
        lines.append(label)
        lines.append("")

        for p in pubs_by_section[sec_key]:
            lines.append(f"▶️ {p.organ or 'Órgão'}")
            lines.append(f"📌 {clean_title(p.type) or 'Ato'}")
            summary_clean = clean_html_text(p.summary) if p.summary else ""
            if summary_clean:
                lines.append(f"_{summary_clean}_") 
            reason = p.relevance_reason or "Para conhecimento."
            prefix = "⚓"
            if "erro" in reason.lower() and "ia" in reason.lower(): prefix = "⚠️"
            lines.append(f"{prefix} {reason}")
            lines.append("")

    return "\n".join(lines)

def monta_valor_whatsapp(pubs: List[ValorPublicacao], when: str) -> str:
    lines = [f"Notícias Valor Econômico ({when}):", ""]
    for p in pubs:
        lines.append(f"▶️ {p.titulo}")
        lines.append(f"📌 {p.link}")
        lines.append(f"⚓ {p.analise_ia}")
        lines.append("")
    return "\n".join(lines)

# Vou manter a estrutura para não cortar lógica importante
def process_grouped_materia(main_article: BeautifulSoup, full_text_content: str, custom_keywords: List[str]) -> Optional[Publicacao]:
    # 1. Extração do Órgão
    organ = norm(main_article.get("artCategory", ""))
    
    # Filtros de Órgão
    organ_lower = organ.lower()
    is_central_budget_organ = any(x in organ_lower for x in ["planejamento", "orçamento", "fazenda", "gestão", "economia", "presidência"])
    
    if not is_central_budget_organ:
        if "comando da aeronáutica" in organ_lower or "comando do exército" in organ_lower:
            return None
        
    section = (main_article.get("pubName", "") or "").upper()
    body = main_article.find("body")
    if not body: return None

    # Transforma o objeto Article em String Bruta para aplicar Regex
    # Isso ignora problemas de parsing do BeautifulSoup com CDATA/XML malformado
    raw_article_str = str(main_article)

    # --- TÍTULO (IDENTIFICA) - VERSÃO BLINDADA ---
    act_type = ""
    
    # Regex Agressivo: Aceita <Identifica>, <ns:Identifica>, <Identifica class="x">, etc.
    # O [^>]* permite qualquer atributo. O re.DOTALL permite quebras de linha no conteúdo.
    match_id = re.search(r"<(?:\w+:)?Identifica[^>]*>(.*?)</(?:\w+:)?Identifica>", raw_article_str, re.DOTALL | re.IGNORECASE)
    
    if match_id:
        content = match_id.group(1)
        # Limpa CDATA e HTML interno (ex: <p>) de uma só vez
        content = content.replace("<![CDATA[", "").replace("]]>", "")
        act_type = norm(clean_html_text(content))
    
    # Fallback 1: Tenta classes HTML se o Regex XML falhar
    if not act_type:
        p_identifica = body.find("p", class_="identifica")
        if p_identifica:
            act_type = norm(p_identifica.get_text(" ", strip=True))

    # Fallback 2: Atributos do artigo (Último caso)
    if not act_type:
        act_type = norm(main_article.get("name", "")) or norm(main_article.get("artType", "Ato Administrativo"))
    
    if not act_type: return None
    

    # --- EMENTA (RESUMO) - VERSÃO BLINDADA ---
    summary = ""
    
    # Regex Agressivo para Ementa
    match_em = re.search(r"<(?:\w+:)?Ementa[^>]*>(.*?)</(?:\w+:)?Ementa>", raw_article_str, re.DOTALL | re.IGNORECASE)
    
    if match_em:
        content = match_em.group(1)
        content = content.replace("<![CDATA[", "").replace("]]>", "")
        summary = norm(clean_html_text(content))

    # Fallback 1: Classe HTML
    if not summary:
        p_ementa = body.find("p", class_="ementa")
        if p_ementa:
            summary = norm(p_ementa.get_text(" ", strip=True))
            
    # Texto completo (para o parser da IA e Keywords)
    display_text = norm(body.get_text(" ", strip=True))
    
    # Fallback 2: Busca por "EMENTA:" no texto ou pega as primeiras linhas
    if not summary:
        match = re.search(r"EMENTA:(.*?)(Vistos|ACORDAM|RESOLVE)", display_text, re.DOTALL | re.I)
        if match: 
            summary = norm(match.group(1))
        else: 
            summary = extract_fallback_summary(display_text)
    
    # --- FIM DA CORREÇÃO ---
    
    # Lógica de Relevância
    is_relevant = False
    reason = None
    
    # --- 1. LIMPEZA PROFUNDA (REGEX) ANTES DE QUALQUER VERIFICAÇÃO ---
    # Remove tags XML estruturais (ex: <Assina>nome</Assina> ou <materia:Cargo>cargo</materia:Cargo>)
    texto_limpo = re.sub(
        r"<(?:\w+:)?(?:Assina|Cargo|Identifica-Signatario)[^>]*>.*?</(?:\w+:)?(?:Assina|Cargo|Identifica-Signatario)>", 
        "", 
        full_text_content, 
        flags=re.IGNORECASE | re.DOTALL
    )
    # Remove tags HTML de parágrafo/div com classes de assinatura (ex: <p class="assina">nome</p>)
    texto_limpo = re.sub(
        r"<(?:p|div)[^>]*class=[\"']?(?:assina|cargo|identifica-signatario)[\"']?[^>]*>.*?</(?:p|div)>", 
        "", 
        texto_limpo, 
        flags=re.IGNORECASE | re.DOTALL
    )

    # 2. AGORA SIM, cria a string de busca a partir do texto SEM assinaturas
    search_content_lower = norm(texto_limpo).lower()
    clean_text_for_ia = ""
    is_mpo_navy_hit_flag = False
    
    found_tags = []
    if MPO_NAVY_TAGS:
        for code, desc in MPO_NAVY_TAGS.items():
            if code in search_content_lower:
                found_tags.append(f"{code}")
    
    if "DO1" in section:
        if is_central_budget_organ:
            if found_tags:
                is_relevant = True
                is_mpo_navy_hit_flag = True
                tags_str = ", ".join(found_tags[:3])
                reason = f"Ato do MPO/Fazenda com impacto direto nas UGs: {tags_str}..."
            elif any(n in search_content_lower for n in ["comando da marinha", "fundo naval", "defesa", "amazul"]):
                is_relevant = True
                is_mpo_navy_hit_flag = True
                reason = "Ato Financeiro/Orçamentário com menção nominal à Marinha/Defesa."
            elif "crédito suplementar" in search_content_lower or "abre aos orçamentos" in search_content_lower:
                is_relevant = True
                is_mpo_navy_hit_flag = True
                reason = "Ato de Crédito Orçamentário (Captura Preventiva)."

        if not is_relevant:
            for kw in KEYWORDS_DIRECT_INTEREST_S1:
                if kw.lower() in search_content_lower:
                    is_relevant = True
                    reason = f"Menção a termo chave: '{kw}'."
                    break
        
        if not is_relevant and is_central_budget_organ:
            if any(bkw in search_content_lower for bkw in BUDGET_KEYWORDS_S1):
                is_relevant = True
                reason = "Ato orçamentário geral."

    elif "DO2" in section:
        # Usa o texto limpo para criar o parser final
        try: soup_copy = BeautifulSoup(texto_limpo, "lxml-xml")
        except: soup_copy = BeautifulSoup(texto_limpo, "html.parser")
        
        clean_search_content_lower = norm(soup_copy.get_text(strip=True)).lower()
        
        for term in TERMS_AND_ACRONYMS_S2:
            if term.lower() in clean_search_content_lower:
                is_relevant = True
                reason = f"Pessoal: Menção a '{term}'."
                break
        
        if not is_relevant:
            for name in NAMES_TO_TRACK:
                if name.lower() in clean_search_content_lower:
                    is_relevant = True
                    reason = f"Pessoal: Menção a '{name}'."
                    break
    
    if custom_keywords:
        for kw in custom_keywords:
            if kw and kw.lower() in search_content_lower:
                is_relevant = True
                reason = f"Keyword personalizada: '{kw}'."
                break
            
    if is_relevant:
        try: soup_full_clean = BeautifulSoup(texto_limpo, "lxml-xml")
        except: soup_full_clean = BeautifulSoup(texto_limpo, "html.parser")
        clean_text_for_ia = norm(soup_full_clean.get_text(strip=True))
        return Publicacao(
            organ=organ, type=act_type, summary=summary, raw=display_text, relevance_reason=reason,
            section=section, clean_text=clean_text_for_ia, is_mpo_navy_hit=is_mpo_navy_hit_flag,
        )
    return None

# =====================================================================================
# ENDPOINT DO ASSISTENTE DRIVE
# =====================================================================================

@app.post("/chat-drive")
async def chat_drive(pergunta: str = Form(...)):
    # Endpoint de comunicação com o Google Drive
    if not GEMINI_API_KEY:
        raise HTTPException(500, "GEMINI_API_KEY não configurada.")
    
    service = get_drive_service()
    if not service:
        # Retorna mensagem amigável se não estiver configurado
        return {"resposta": "⚠️ O acesso ao Drive não está configurado no servidor. Verifique a variável GOOGLE_SERVICE_ACCOUNT_JSON."}

    try:
        # 1. Gemini extrai palavras-chave para busca eficiente
        model = genai.GenerativeModel("gemini-3-pro-preview")
        kw_prompt = f"O usuário perguntou: '{pergunta}'. Extraia apenas as 2 palavras-chave mais importantes para buscar o arquivo. Responda APENAS as palavras."
        kw_res = await model.generate_content_async(kw_prompt)
        search_query = kw_res.text.strip()

        # 2. Busca no Drive (Nome ou Conteúdo Texto)
        q_clean = search_query.replace("'", "")
        query_drive = f"name contains '{q_clean}' or fullText contains '{q_clean}'"
        
        results = service.files().list(
            q=query_drive,
            fields="files(id, name, modifiedTime, webViewLink)",
            orderBy="modifiedTime desc",
            pageSize=5
        ).execute()
        
        files = results.get('files', [])
        
        if not files:
            return {"resposta": f"Não encontrei arquivos recentes no Drive contendo '{q_clean}'."}

        # 3. Prepara contexto para o Gemini responder
        contexto_drive = ""
        for f in files:
            dt_iso = f.get('modifiedTime', '')
            contexto_drive += f"- Arquivo: {f['name']} (Data: {dt_iso}) - Link: {f['webViewLink']}\n"

        final_prompt = f"""
Você é o assistente virtual da Marinha (CORM).
O usuário perguntou: "{pergunta}"

Eu busquei no Drive e encontrei estes arquivos (do mais recente para o antigo):
{contexto_drive}

Com base APENAS nisso, responda à pergunta do usuário. 
Se ele pediu o "último" ou "mais recente", considere a data de modificação.
Forneça o nome do arquivo e o link se possível.
"""
        
        response = await model.generate_content_async(final_prompt)
        return {"resposta": response.text}

    except Exception as e:
        print(f"Erro Chat Drive: {e}")
        return {"resposta": f"Erro ao processar sua solicitação no Drive: {str(e)}"}


# =====================================================================================
# ENDPOINTS AUXILIARES (INLABS, ETC)
# =====================================================================================

async def inlabs_login_and_get_session() -> httpx.AsyncClient:
    if not INLABS_USER or not INLABS_PASS:
        raise HTTPException(500, "Config ausente: INLABS_USER e INLABS_PASS.")
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    try: await client.get(INLABS_BASE)
    except Exception: pass
    r = await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS})
    if r.status_code >= 400:
        await client.aclose()
        raise HTTPException(502, f"Falha de login no INLABS: HTTP {r.status_code}")
    return client

async def resolve_date_url(client: httpx.AsyncClient, date: str) -> str:
    r = await client.get(INLABS_BASE)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    cand_texts = [date, date.replace("-", "_"), date.replace("-", "")]
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        txt = (a.get_text() or "").strip()
        hay = (txt + " " + href).lower()
        if any(c.lower() in hay for c in cand_texts):
            return urljoin(INLABS_BASE.rstrip("/") + "/", href.lstrip("/"))
    fallback_url = f"{INLABS_BASE.rstrip('/')}/{date}/"
    rr = await client.get(fallback_url)
    if rr.status_code == 200: return fallback_url
    raise HTTPException(404, f"Não encontrei a pasta/listagem da data {date}.")

async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    base_url = await resolve_date_url(client, date)
    r = await client.get(base_url)
    if r.status_code >= 400: raise HTTPException(502, f"Falha ao abrir listagem {base_url}")
    return r.text

def pick_zip_links_from_listing(html: str, base_url_for_rel: str, only_sections: List[str]) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    wanted = set(s.strip().upper() for s in only_sections) if only_sections else {"DO1"}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = (a.get_text() or "").strip().upper()
        if href.lower().endswith(".zip"):
            full_url = urljoin(base_url_for_rel, href)
            filename = href.split("/")[-1].upper()
            is_match = False
            for sec in wanted:
                if sec in filename or sec in text:
                    is_match = True
                    break
            if is_match:
                links.append(full_url)
    return sorted(list(set(links)))

async def download_zip(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(502, f"Falha ao baixar ZIP {url}")
    return r.content

def extract_xml_from_zip(zip_bytes: bytes) -> List[bytes]:
    xml_blobs: List[bytes] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for name in z.namelist():
                if name.lower().endswith(".xml"):
                    xml_blobs.append(z.read(name))
    except zipfile.BadZipFile: pass
    return xml_blobs

@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2"),
    keywords_json: Optional[str] = Form(None),
):
    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]
    custom_keywords: List[str] = []
    if keywords_json:
        try:
            kl = json.loads(keywords_json)
            if isinstance(kl, list): custom_keywords = [str(k).strip().lower() for k in kl if str(k).strip()]
        except: pass

    pubs_final: List[Publicacao] = []
    usou_fallback = False
    
    print(f">>> Tentando InLabs (v21.3) para {data}...")
    try:
        client = await inlabs_login_and_get_session()
        try:
            listing_url = await resolve_date_url(client, data)
            html = await fetch_listing_html(client, data)
            zip_links = pick_zip_links_from_listing(html, listing_url, secs)
            
            if not zip_links: raise HTTPException(404, detail="ZIPs não encontrados.")

            for zurl in zip_links:
                print(f"Baixando {zurl}...")
                zb = await download_zip(client, zurl)
                all_new_xml_blobs = extract_xml_from_zip(zb)
                materias: Dict[str, Dict[str, Any]] = {}
                
                for blob in all_new_xml_blobs:
                    try:
                        soup = BeautifulSoup(blob, "lxml-xml")
                        article = soup.find("article")
                        if not article: continue
                        
                        materia_id = article.get("idMateria")
                        if not materia_id: continue
                        
                        # O dicionário agora guarda um 'score' para saber qual arquivo tem a maior Ementa
                        if materia_id not in materias:
                            materias[materia_id] = {"main_article": None, "full_text": "", "score": -1}
                            
                        # Junta o texto de todos os anexos para bater nas keywords (52131, 52000, etc)
                        materias[materia_id]["full_text"] += (blob.decode("utf-8", errors="ignore") + "\n")
                        
                        body = article.find("body")
                        if body:
                            # Lê as tags REAIS usando o BeautifulSoup
                            ementa_tag = article.find("Ementa")
                            identifica_tag = article.find("Identifica")
                            
                            # Pega apenas o texto puro (ignorando tags e CDATA)
                            ementa_text = ementa_tag.text.strip() if ementa_tag and ementa_tag.text else ""
                            identifica_text = identifica_tag.text.strip() if identifica_tag and identifica_tag.text else ""
                            
                            # Soma a quantidade de letras úteis
                            score = len(ementa_text) + len(identifica_text)
                            
                            # Se este arquivo tiver um texto de Ementa MAIOR que o anterior, ele assume como principal!
                            if materias[materia_id]["main_article"] is None or score > materias[materia_id]["score"]:
                                materias[materia_id]["main_article"] = article
                                materias[materia_id]["score"] = score
                    except: continue
                
                for materia_id, content in materias.items():
                    if content["main_article"]:
                        publication = process_grouped_materia(content["main_article"], content["full_text"], custom_keywords)
                        if publication:
                            pubs_final.append(publication)
        finally:
            await client.aclose()

    except Exception as e:
        print(f"⚠️ Falha no InLabs: {e}")
        usou_fallback = True

    if usou_fallback and executar_fallback:
        try:
            fb_results = await executar_fallback(data, custom_keywords)
            for item in fb_results:
                pubs_final.append(Publicacao(
                    organ=item['organ'], type=item['type'], summary=item['summary'],
                    raw=item['raw'], relevance_reason=item['relevance_reason'] + " (Fallback)",
                    section=item['section'], clean_text=item['raw'], is_parsed_mpo=False
                ))
        except Exception: pass

    seen: Set[str] = set()
    merged: List[Publicacao] = []
    for p in pubs_final:
        key = f"{p.organ}-{p.type}-{str(p.summary)[:50]}"
        if key not in seen:
            seen.add(key)
            merged.append(p)

    texto = monta_whatsapp(merged, data)
    return ProcessResponse(date=data, count=len(merged), publications=merged, whatsapp_text=texto)

@app.post("/processar-dou-ia", response_model=ProcessResponse)
async def processar_dou_ia(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2"),
    keywords_json: Optional[str] = Form(None),
):
    if not GEMINI_API_KEY: raise HTTPException(500, detail="GEMINI_API_KEY não definida.")
    try: model = genai.GenerativeModel("gemini-3-pro-preview") 
    except Exception as e: raise HTTPException(500, detail=f"Falha IA: {e}")

    res_padrao = await processar_inlabs(data, sections, keywords_json)
    pubs_analisadas = []
    tasks = []
    
    for p in res_padrao.publications:
        # Se for Seção 2, usa o prompt exclusivo de pessoal e injeta o alvo
        if p.section and ("DO2" in p.section or "Seção 2" in p.section):
            # Extrai do sistema QUEM fez a matéria ser capturada
            alvo_identificado = p.relevance_reason or "Militar/Servidor de interesse"
            prompt = GEMINI_PESSOAL_PROMPT + f"\n\n[ALVO IDENTIFICADO PELO SISTEMA: {alvo_identificado}]\nDescreva apenas a ação referente a este alvo."
        # Se for Seção 1/Outros, mantém a lógica original
        else:
            prompt = GEMINI_MPO_PROMPT if p.is_mpo_navy_hit else GEMINI_MASTER_PROMPT
            
        tasks.append(analyze_single_pub(p, model, prompt))

    results = await asyncio.gather(*tasks)
    
    for p_res in results:
        if p_res: pubs_analisadas.append(p_res)
            
    texto_final = monta_whatsapp(pubs_analisadas, data)
    return ProcessResponse(date=data, count=len(pubs_analisadas), publications=pubs_analisadas, whatsapp_text=texto_final)

async def analyze_single_pub(pub: Publicacao, model, prompt_template):
    try:
        analysis = await get_ai_analysis(pub.clean_text or pub.raw, model, prompt_template)
        if analysis:
            # Verifica se é uma publicação da Seção 2
            is_secao_2 = pub.section and ("DO2" in pub.section or "Seção 2" in pub.section)
            
            # Só descarta se disser "sem impacto" E NÃO for MPO E NÃO for Seção 2
            if "sem impacto" in analysis.lower() and not pub.is_mpo_navy_hit and not is_secao_2: 
                return None
                
            pub.relevance_reason = analysis
        return pub
    except: return pub

async def get_ai_analysis(clean_text: str, model: genai.GenerativeModel, prompt_template: str) -> Optional[str]:
    try:
        prompt = f"{prompt_template}\n\n{clean_text[:12000]}"
        response = await model.generate_content_async(prompt)
        return norm(response.text)
    except Exception as e:
        print(f"Erro IA: {e}")
        return None

# --- ROTAS DE LEGISLATIVO E PAC (ATUALIZADO COM FIX TELEGRAM) ---

class TrackRequest(BaseModel):
    uid: str
    casa: str
    tipo: str
    numero: str
    ano: str
    ementa: str
    link: str

@app.post("/legislativo/track")
async def track_proposition(item: TrackRequest):
    res = toggle_tracking(item.dict())
    return {"status": "ok", "action": res}

@app.get("/legislativo/watchlist")
async def get_watchlist():
    wl = load_watchlist()
    return list(wl.values())

@app.post("/legislativo/force-update")
async def force_update_legis():
    # Chama com commit=True para salvar, mas captura o retorno
    updates = await check_tramitacoes_watchlist(commit=True)
    
    # Se houver novidades, o site AGORA envia o Telegram!
    if updates:
        print(f"[API] Updates encontrados: {len(updates)}. Enviando Telegram...")
        msg = ["🏛️ *Atualização de Tramitação (Manual/Site)*", ""]
        for up in updates:
            msg.append(f"📌 *{up['titulo']}*\n📝 {up['ementa'][:100]}...\n🔄 Status: {up['status']}\n🔗 [Link]({up['link']})\n")
        
        await send_telegram_message("\n".join(msg))
    
    return {"updates_found": len(updates), "data": updates}

class ManualSearch(BaseModel):
    casa: str
    sigla: str
    numero: str
    ano: str

@app.post("/legislativo/add-manual")
async def add_manual_proposition(search: ManualSearch):
    found_item = await find_proposition(search.casa, search.sigla, search.numero, search.ano)
    if not found_item:
        return {"status": "error", "message": "Proposição não encontrada nas bases oficiais."}
    toggle_tracking(found_item)
    return {"status": "ok", "message": f"Projeto {found_item['tipo']} {found_item['numero']} adicionado!", "data": found_item}

@app.post("/processar-valor-ia", response_model=ProcessResponseValor)
async def processar_valor_ia(data: str = Form(...)):
    from api import run_valor_analysis, monta_valor_whatsapp
    pubs_list, _ = await run_valor_analysis(data, use_state=False)
    pubs_model = [ValorPublicacao(**p) for p in pubs_list]
    return ProcessResponseValor(date=data, count=len(pubs_model), publications=pubs_model, whatsapp_text=monta_valor_whatsapp(pubs_model, data))

@app.post("/teste-fallback", response_model=ProcessResponse)
async def teste_fallback(data: str = Form(...), keywords_json: Optional[str] = Form(None)):
    if not executar_fallback: raise HTTPException(500, detail="Módulo 'dou_fallback.py' não encontrado.")
    custom_keywords = []
    if keywords_json:
        try:
            kl = json.loads(keywords_json)
            if isinstance(kl, list): custom_keywords = [str(k).strip().lower() for k in kl if str(k).strip()]
        except: pass
    try:
        fb_results = await executar_fallback(data, custom_keywords)
    except Exception as e: raise HTTPException(500, detail=str(e))
    pubs = [Publicacao(organ=i['organ'], type=i['type'], summary=i['summary'], raw=i['raw'], relevance_reason=i['relevance_reason'], section=i['section'], clean_text=i['raw']) for i in fb_results]
    return ProcessResponse(date=data, count=len(pubs), publications=pubs, whatsapp_text=monta_whatsapp(pubs, data))

async def crawl_valor_headlines(cover_url: str, date_str: str) -> List[Dict[str, str]]:
    print(f"[Valor Crawler] Acessando capa: {cover_url}")
    found_articles = []
    date_clean = date_str.replace("-", "") 
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = await client.get(cover_url, headers=headers)
            if r.status_code != 200: return []
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                title = norm(a.get_text())
                if date_clean in href and len(href) > len(f"/impresso/{date_clean}/"):
                    full_link = href if href.startswith("http") else f"https://valor.globo.com{href}"
                    if title and len(title) > 10 and not any(f['link'] == full_link for f in found_articles):
                         found_articles.append({"title": title, "link": full_link})
            return found_articles
        except Exception: return []

SEARCH_QUERIES = ['"contas publicas" OR "politica fiscal"', '"orcamento" OR "LDO" OR "LOA"', '"economia" OR "defesa" OR "marinha"']

async def run_valor_analysis(today_str: str, use_state: bool = True) -> (List[Dict[str, Any]], Set[str]):
    if not GEMINI_API_KEY: return [], set()
    genai.configure(api_key=GEMINI_API_KEY)
    try: model = genai.GenerativeModel("gemini-3-pro-preview")
    except: return [], set()
    date_suffix = today_str.replace("-", "")
    google_results = []
    for q in SEARCH_QUERIES:
        try:
            res = await perform_google_search(q, search_date=today_str)
            google_results.extend(res)
        except: pass
        await asyncio.sleep(1)
    
    final_articles, processed_links = [], set()
    for res in google_results:
        if res.link.rstrip("/").endswith(date_suffix):
            crawled = await crawl_valor_headlines(res.link, today_str)
            for news in crawled:
                if news['link'] not in processed_links:
                    final_articles.append(news); processed_links.add(news['link'])
        else:
            if res.link not in processed_links:
                final_articles.append({"title": res.title, "link": res.link}); processed_links.add(res.link)
    
    pubs_finais, links_encontrados = [], set()
    for item in final_articles:
        text_check = item['title'].lower()
        if any(k in text_check for k in ["orçamento", "fiscal", "defesa", "marinha", "gasto", "corte", "economia"]):
            ai_reason = await get_ai_analysis(f"TÍTULO: {item['title']}", model, GEMINI_VALOR_PROMPT)
            links_encontrados.add(item['link'])
            if ai_reason and "sem impacto" not in ai_reason.lower():
                pubs_finais.append({"titulo": item['title'], "link": item['link'], "analise_ia": ai_reason})
    return pubs_finais, links_encontrados

# --- Lógica PAC (Atualizada para CSV ROBUSTO) ---
PROGRAMAS_ACOES_PAC = {
    'PROSUB': {'123G': 'ESTALEIRO E BASE NAVAL', '123H': 'SUBMARINO NUCLEAR', '123I': 'SUBMARINOS CONVENCIONAIS'},
    'PNM': {'14T7': 'TECNOLOGIA NUCLEAR'}, 'PRONAPA': {'1N47': 'NAVIOS-PATRULHA'}
}

async def load_pac_data_source():
    content = ""
    # 1. Tenta baixar da URL
    if PAC_DATA_URL:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(PAC_DATA_URL)
                if r.status_code == 200: content = r.text
        except Exception: pass
    
    # 2. Tenta ler local
    if not content and os.path.exists(PAC_DATA_FILE):
        try:
            with open(PAC_DATA_FILE, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except Exception: pass
            
    return content

def parse_float_br(val: Any) -> float:
    if not val: return 0.0
    s_val = str(val).strip().replace("R$", "").replace(" ", "")
    if not s_val: return 0.0
    if ',' in s_val and '.' in s_val:
        s_val = s_val.replace(".", "").replace(",", ".")
    elif ',' in s_val:
        s_val = s_val.replace(",", ".")
    try: return float(s_val)
    except: return 0.0

def parse_pac_csv(csv_content: str) -> Dict[int, Dict[str, Any]]:
    MONTHS = {"JAN":1, "FEV":2, "MAR":3, "ABR":4, "MAI":5, "JUN":6, "JUL":7, "AGO":8, "SET":9, "OUT":10, "NOV":11, "DEZ":12}
    data_store = {} 
    if not csv_content: return {}
    delimiter = ','
    first_line = csv_content.split('\n')[0]
    if ';' in first_line and first_line.count(';') > first_line.count(','): delimiter = ';'
    
    f = io.StringIO(csv_content)
    reader = csv.DictReader(f, delimiter=delimiter)
    
    norm_map = {x.strip().upper(): x for x in (reader.fieldnames or [])}
        
    for row in reader:
        def get_val(key_fragment):
            for norm_key, orig_key in norm_map.items():
                if key_fragment in norm_key: return row.get(orig_key)
            return None

        mes_lanc = str(get_val("MÊS") or get_val("MES") or "").strip().upper()
        if "/" not in mes_lanc: continue
        try:
            m_str, y_str = mes_lanc.split("/")
            year = int(y_str)
            month = MONTHS.get(m_str[:3], 0)
            if month == 0: continue
        except: continue
        
        acao_gov = str(get_val("AÇÃO") or get_val("ACAO") or "").strip()
        if not acao_gov: continue
        
        def pf(key_part): return parse_float_br(get_val(key_part))

        vals = {
            "DOTAÇÃO ATUAL": pf("ATUALIZADA"),
            "PROVISIONADO (P)": pf("PROVISAO"),
            "DESTAQUE (D)": pf("DESTAQUE"),
            "EMPENHADO": pf("EMPENHADA"),
            "PAGO": pf("PAGAS")
        }
        vals["P+D"] = vals["PROVISIONADO (P)"] + vals["DESTAQUE (D)"]
        
        if year not in data_store: data_store[year] = {}
        if acao_gov not in data_store[year] or month >= data_store[year][acao_gov]['month']:
            data_store[year][acao_gov] = {'month': month, 'data': vals}
            
    return data_store
 
@app.get("/api/pac-data/historical-dotacao")
async def get_pac_historical():
    csv_text = await load_pac_data_source()
    parsed_data = parse_pac_csv(csv_text)
    if not parsed_data: return {"labels": [], "datasets": []}
    years = sorted([y for y in parsed_data.keys() if 2010 <= y <= 2030])
    datasets_map = {}
    all_actions = []
    for prog, acoes in PROGRAMAS_ACOES_PAC.items():
        for cod, desc in acoes.items():
            all_actions.append((cod, desc))
            datasets_map[cod] = []

    for y in years:
        y_data = parsed_data.get(y, {})
        for cod, desc in all_actions:
            val = y_data.get(cod, {}).get('data', {}).get('DOTAÇÃO ATUAL', 0.0)
            datasets_map[cod].append(val)
            
    datasets = []
    for cod, desc in all_actions:
        datasets.append({"label": f"{cod} - {desc}", "data": datasets_map[cod]})
        
    return {"labels": years, "datasets": datasets}

@app.get("/api/pac-data/{ano}")
async def get_pac_data(ano: int = Path(..., ge=2010, le=2030)):
    csv_text = await load_pac_data_source()
    parsed_data = parse_pac_csv(csv_text)
    year_data = parsed_data.get(ano, {})
    tabela = []
    total_keys = ['DOTAÇÃO ATUAL', 'PROVISIONADO (P)', 'DESTAQUE (D)', 'P+D', 'EMPENHADO', 'PAGO']
    total_geral = {k: 0.0 for k in total_keys}

    for prog, acoes in PROGRAMAS_ACOES_PAC.items():
        soma_prog = {k: 0.0 for k in total_keys}
        linhas = []
        for cod, desc in acoes.items():
            dados_acao = year_data.get(cod, {}).get('data', {})
            vals = {k: dados_acao.get(k, 0.0) for k in total_keys}
            pd = vals['P+D']
            percent = (vals['EMPENHADO'] / pd * 100) if pd > 0 else 0.0
            vals['% EXEC (P+D)'] = percent
            linhas.append({'PROGRAMA': None, 'AÇÃO': f"{cod} - {desc}", **vals})
            for k in total_keys: soma_prog[k] += vals[k]

        pd_prog = soma_prog['P+D']
        soma_prog['% EXEC (P+D)'] = (soma_prog['EMPENHADO'] / pd_prog * 100) if pd_prog > 0 else 0.0
        tabela.append({'PROGRAMA': prog, 'AÇÃO': None, **soma_prog})
        tabela.extend(linhas)
        for k in total_keys: total_geral[k] += soma_prog[k]

    pd_total = total_geral['P+D']
    total_geral['% EXEC (P+D)'] = (total_geral['EMPENHADO'] / pd_total * 100) if pd_total > 0 else 0.0
    tabela.append({'PROGRAMA': 'Total Geral', 'AÇÃO': None, **total_geral})
    return tabela
    
@app.post("/api/admin/force-update-pac")
async def force_update_pac():
    await load_pac_data_source()
    return {"status": "OK, CSV Reloaded"}

@app.get("/debug-csv")
async def debug_csv():
    if not os.path.exists(PAC_DATA_FILE): return {"error": f"Arquivo {PAC_DATA_FILE} não encontrado."}
    rows = []
    try:
        with open(PAC_DATA_FILE, "r", encoding="utf-8-sig") as f:
            delimiter = ';' if ';' in f.readline() else ','
            f.seek(0)
            reader = csv.reader(f, delimiter=delimiter)
            headers = next(reader, [])
            for _ in range(5):
                try: rows.append(next(reader))
                except StopIteration: break
        return {"headers": headers, "first_rows": rows}
    except Exception as e: return {"error": str(e)}

@app.post("/processar-legislativo")
async def endpoint_legislativo(days: int = Form(5)):
    try:
        func = globals().get('check_and_process_legislativo')
        if not func:
            import check_legislativo
            func = check_legislativo.check_and_process_legislativo
        res = await func(only_new=False, days_back=days)
        if not res: return {"count": 0, "message": "Nenhuma proposição encontrada.", "data": []}
        return {"count": len(res), "message": f"Encontradas {len(res)} proposições.", "data": res}
    except Exception as e:
        print(f"Erro Legis: {e}")
        raise HTTPException(500, str(e))

@app.get("/health")
async def health(): return {"status": "ok", "ts": datetime.now().isoformat()}

@app.get("/test-ia")
async def test_ia():
    if not GEMINI_API_KEY: raise HTTPException(500, "Sem Key")
    try:
        m = genai.GenerativeModel("gemini-3-pro-preview")
        r = await m.generate_content_async("Teste")
        return {"ok": True, "resp": r.text}
    except Exception as e: return {"ok": False, "err": str(e)}

if os.path.isdir("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
else: print("⚠️ Pasta 'static' não encontrada.")
