# Nome do arquivo: api.py
# Versão: 17.4 (Limpeza de HTML Residual em Resumos)

from fastapi import FastAPI, Form, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles 
from pydantic import BaseModel
from typing import List, Optional, Set, Dict, Any
from datetime import datetime
import os, io, zipfile, json, re
from urllib.parse import urljoin
import asyncio

import httpx
from bs4 import BeautifulSoup

# IA / Gemini
import google.generativeai as genai

try:
    from google_search import perform_google_search, SearchResult
except ImportError:
    pass

import numpy as np
try:
    from orcamentobr import despesa_detalhada
    from check_pac import update_pac_historical_cache, HISTORICAL_CACHE_PATH
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

app = FastAPI(title="Robô DOU/Valor API - v17.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    print(">>> SISTEMA UNIFICADO INICIADO (v17.4 - HTML Clean) <<<")
    try:
        if not os.path.exists(HISTORICAL_CACHE_PATH):
            asyncio.create_task(update_pac_historical_cache())
    except Exception as e:
        print(f"Erro ao verificar cache PAC: {e}")

    try:
        from run_check import main_loop
        asyncio.create_task(main_loop())
        print(">>> Loop de verificação iniciado.")
    except ImportError:
        pass

# =====================================================================================
# CONFIGURAÇÕES
# =====================================================================================

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

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

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
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil.
Sua tarefa é ler a publicação do DOU e escrever UMA frase curta (max 2 linhas) para relatório WhatsApp.
Foque em: Créditos Suplementares, Alteração de Limites (LME), Bloqueios, Fontes de Recursos e Pessoal Chave.
Se for trivial, diga: "Sem impacto direto."
"""

GEMINI_MPO_PROMPT = """
Você é analista orçamentário da Marinha. Esta é uma portaria do MPO/Fazenda.
Identifique no texto:
1. Se há Suplementação ou Cancelamento para a Defesa (UG 52xxx) ou Marinha.
2. Valores envolvidos (se explícitos).
3. Se é alteração de Cronograma Financeiro ou Limites.
Responda de forma direta e técnica.
"""

GEMINI_VALOR_PROMPT = "Analista financeiro da Marinha. Resumo de 1 frase sobre impacto para Defesa/Orçamento."

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
    """Limpa títulos sujos vindos do nome do arquivo."""
    t = raw_title
    t = t.replace("nA", "Nº").replace("na", "Nº")
    t = t.replace(".2025", "/2025").replace(".2024", "/2024")
    t = t.replace("GM.MPO", "GM/MPO").replace("e an", "")
    t = t.replace("_", " ").replace(".doc", "").replace(".xml", "")
    t = re.sub(r"-\d+$", "", t)
    return norm(t)

def clean_html_text(raw_text: str) -> str:
    """
    Remove tags HTML residuais (<table>, <p>, etc) que vêm do CDATA do XML.
    Retorna apenas o texto limpo legível.
    """
    if not raw_text or "<" not in raw_text:
        return raw_text
    try:
        # Força o parser de HTML na string extraída
        soup = BeautifulSoup(raw_text, "html.parser")
        return soup.get_text(" ", strip=True)
    except:
        return raw_text

def extract_fallback_summary(text: str) -> str:
    """Tenta extrair um resumo quando a Ementa está vazia, limpando HTML."""
    
    # 1. Limpa o HTML primeiro (Fundamental para tabelas MPO)
    clean_txt = clean_html_text(text)
    
    # 2. Tenta pegar o primeiro parágrafo significativo
    match = re.search(r"(Abre aos? Orçamentos?.*?vigente\.?)", clean_txt, re.IGNORECASE | re.DOTALL)
    if match: return norm(match.group(1))
    
    match2 = re.search(r"(Altera.*?providências\.?)", clean_txt, re.IGNORECASE | re.DOTALL)
    if match2: return norm(match2.group(1))

    match3 = re.search(r"^.*?(?:RESOLVE:?|DECIDE:?)(.*)", clean_txt, re.DOTALL | re.IGNORECASE)
    if match3:
        candidate = match3.group(1).strip()
        return candidate[:300] + ("..." if len(candidate) > 300 else "")

    # 3. Fallback final: Início do texto limpo
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
            
            # Limpa o resumo antes de exibir no WhatsApp
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

def process_grouped_materia(
    main_article: BeautifulSoup,
    full_text_content: str,
    custom_keywords: List[str],
) -> Optional[Publicacao]:
    organ = norm(main_article.get("artCategory", ""))
    organ_lower = organ.lower()
    
    is_central_budget_organ = any(x in organ_lower for x in ["planejamento", "orçamento", "fazenda", "gestão", "economia", "presidência"])
    
    if not is_central_budget_organ:
        if "comando da aeronáutica" in organ_lower or "comando do exército" in organ_lower:
            return None
        
    section = (main_article.get("pubName", "") or "").upper()
    body = main_article.find("body")
    if not body: return None
        
    identifica_node = body.find("Identifica")
    act_type = norm(identifica_node.get_text(strip=True)) if identifica_node else ""
    if not act_type:
        act_type = norm(main_article.get("name", "")) or norm(main_article.get("artType", "Ato Administrativo"))
    
    if not act_type: return None
    
    summary = norm(body.find("Ementa").get_text(strip=True) if body.find("Ementa") else "")
    display_text = norm(body.get_text(strip=True))
    
    if not summary:
        match = re.search(r"EMENTA:(.*?)(Vistos|ACORDAM)", display_text, re.DOTALL | re.I)
        if match: 
            summary = norm(match.group(1))
        else:
            # Pega o texto limpo do HTML
            summary = extract_fallback_summary(display_text)
    
    is_relevant = False
    reason = None
    search_content_lower = norm(full_text_content).lower()
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
        try: soup_copy = BeautifulSoup(full_text_content, "lxml-xml")
        except: soup_copy = BeautifulSoup(full_text_content, "html.parser")

        for tag in soup_copy.find_all("p", class_=["assina", "cargo"]):
            tag.decompose()
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
        try: soup_full_clean = BeautifulSoup(full_text_content, "lxml-xml")
        except: soup_full_clean = BeautifulSoup(full_text_content, "html.parser")
            
        clean_text_for_ia = norm(soup_full_clean.get_text(strip=True))
        return Publicacao(
            organ=organ,
            type=act_type,
            summary=summary,
            raw=display_text,
            relevance_reason=reason,
            section=section,
            clean_text=clean_text_for_ia,
            is_mpo_navy_hit=is_mpo_navy_hit_flag,
        )
    
    return None

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
    
    print(f">>> Tentando InLabs (v17.4) para {data}...")
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
                        if materia_id not in materias:
                            materias[materia_id] = {"main_article": None, "full_text": ""}
                        materias[materia_id]["full_text"] += (blob.decode("utf-8", errors="ignore") + "\n")
                        body = article.find("body")
                        if body:
                            materias[materia_id]["main_article"] = article
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
    try: model = genai.GenerativeModel("gemini-2.5-flash") 
    except Exception as e: raise HTTPException(500, detail=f"Falha IA: {e}")

    res_padrao = await processar_inlabs(data, sections, keywords_json)
    pubs_analisadas = []
    tasks = []
    
    for p in res_padrao.publications:
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
            if "sem impacto" in analysis.lower() and not pub.is_mpo_navy_hit: return None
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
    updates = await check_tramitacoes_watchlist()
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
    try: model = genai.GenerativeModel("gemini-2.5-flash")
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

PROGRAMAS_ACOES_PAC = {
    'PROSUB': {'123G': 'ESTALEIRO E BASE NAVAL', '123H': 'SUBMARINO NUCLEAR', '123I': 'SUBMARINOS CONVENCIONAIS'},
    'PNM': {'14T7': 'TECNOLOGIA NUCLEAR'}, 'PRONAPA': {'1N47': 'NAVIOS-PATRULHA'}
}

async def buscar_dados_acao_pac(ano: int, acao_cod: str) -> Optional[Dict[str, Any]]:
    try:
        df_detalhado = await asyncio.to_thread(despesa_detalhada, exercicio=ano, acao=acao_cod, inclui_descricoes=True, ignore_secure_certificate=True)
        if df_detalhado.empty: return None
        cols_possiveis = ['loa', 'loa_mais_credito', 'empenhado', 'liquidado', 'pago', 'dotacao_disponivel', 'saldo_disponivel', 'saldo_dotacao']
        colunas = [c for c in cols_possiveis if c in df_detalhado.columns]
        if not colunas: return None
        totais = df_detalhado[colunas].sum().to_dict()
        totais['Acao_cod'] = acao_cod
        if 'dotacao_disponivel' not in totais:
             totais['dotacao_disponivel'] = totais.get('saldo_disponivel') or totais.get('saldo_dotacao') or 0.0
        return totais
    except: return None
 
@app.get("/api/pac-data/historical-dotacao")
async def get_pac_historical():
    try:
        if os.path.exists(HISTORICAL_CACHE_PATH):
            with open(HISTORICAL_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            return {"labels": [], "datasets": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pac-data/{ano}")
async def get_pac_data(ano: int = Path(..., ge=2010, le=2025)):
    tasks = []
    for prog, acoes in PROGRAMAS_ACOES_PAC.items():
        for acao in acoes.keys(): tasks.append(buscar_dados_acao_pac(ano, acao))
    results = await asyncio.gather(*tasks)
    dados = [r for r in results if r]
    if not dados: return []

    tabela = []
    total = {'LOA':0,'DOTAÇÃO ATUAL':0,'DISPONÍVEL':0,'EMPENHADO (c)':0,'LIQUIDADO':0,'PAGO':0}
    for prog, acoes in PROGRAMAS_ACOES_PAC.items():
        soma = total.copy()
        soma = {k:0 for k in total}
        linhas = []
        for cod, desc in acoes.items():
            row = next((d for d in dados if d.get('Acao_cod') == cod), None)
            def gv(k): return float(row.get(k, 0.0)) if row else 0.0
            vals = {'LOA':gv('loa'),'DOTAÇÃO ATUAL':gv('loa_mais_credito'),'DISPONÍVEL':gv('dotacao_disponivel'),'EMPENHADO (c)':gv('empenhado'),'LIQUIDADO':gv('liquidado'),'PAGO':gv('pago')}
            linhas.append({'PROGRAMA': None, 'AÇÃO': f"{cod} - {desc}", **vals})
            for k,v in vals.items(): soma[k]+=v
        tabela.append({'PROGRAMA': prog, 'AÇÃO': None, **soma})
        tabela.extend(linhas)
        for k,v in soma.items(): total[k]+=v
    tabela.append({'PROGRAMA': 'Total Geral', 'AÇÃO': None, **total})
    return tabela
    
@app.post("/api/admin/force-update-pac")
async def force_update_pac():
    await update_pac_historical_cache()
    return {"status": "OK"}

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
        m = genai.GenerativeModel("gemini-1.5-pro")
        r = await m.generate_content_async("Teste")
        return {"ok": True, "resp": r.text}
    except Exception as e: return {"ok": False, "err": str(e)}

if os.path.isdir("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
else: print("⚠️ Pasta 'static' não encontrada.")
