# -*- coding: utf-8 -*-
"""
Nome do arquivo: mb_portaria_parser.py
Versão: 4.0 (Granularidade de Ação e RP)
Descrição: Parser orçamentário que extrai Ação (PT), RP e detalha Suplementação/Cancelamento.
"""
from __future__ import annotations

import re
import zipfile
import io
from xml.etree import ElementTree as ET
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple, Union, Optional

# --- CONFIGURAÇÃO DE INTERESSE ---

# UGs da Marinha/Defesa (Filtro Principal)
MB_UGS_DEFAULT = {
    "52111", "52121", "52131", # Comandos Militares
    "52133", # SECIRM
    "52232", # CCCPM
    "52233", # AMAZUL
    "52931", # Fundo Naval
    "52932", # FDEPM
    "52000", # Ministério da Defesa
    "52904"  # Fundo do MD (se houver)
}

# Mapeamento de Ações Estratégicas (Para dar nome aos bois)
STRATEGIC_MAP = {
    "123G": "PROSUB (Estaleiro)",
    "123H": "PROSUB (Sub. Nuclear)",
    "123I": "PROSUB (Sub. Convencional)",
    "14T7": "PNM (Nuclear)",
    "1N47": "PRONAPA (Patrulha)",
    "2000": "Adm. Unidade",
    "20X3": "Manutenção",
    "00OQ": "Contribuições Internacionais",
    "0Z00": "Reserva de Contingência",
    "21A0": "Apprendiz",
    "20GP": "Gestão e Política"
}

def _sanitize_html_content(html_str: str) -> str:
    if not html_str: return ""
    s = re.sub(r'\sxmlns="[^"]+"', '', html_str, count=1)
    s = s.replace("&nbsp;", " ").replace("&quot;", '"').replace("&apos;", "'")
    return s

def _html_to_text(html: str) -> str:
    if not html: return ""
    try:
        clean_html = _sanitize_html_content(html)
        root = ET.fromstring(f"<root>{clean_html}</root>")
        txt = " ".join(x.strip() for x in root.itertext() if x.strip())
        return re.sub(r"\s+", " ", txt)
    except:
        return re.sub(r"<[^>]+>", " ", html).strip()

def _extract_header_hint(text: str) -> str:
    """Tenta extrair o resumo/ementa da portaria."""
    if not text: return ""
    patterns = [
        r"(Abre\s+ao?s?\s+Or(ç|c)amentos?[\s\S]*?vigente\.)",
        r"(Altera\s+os\s+limites[\s\S]*?posteriores\.?)",
        r"(Altera\s+mediante\s+remanejamento[\s\S]*?providências\.?)",
        r"(Atualiza\s+os\s+valores[\s\S]*?posteriores\.?)"
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m: return re.sub(r"\s+", " ", m.group(1)).strip()
    
    pre = re.split(r"ANEXO\s+I", text, flags=re.I)[0]
    return pre.strip()[:300].rstrip(" ,;") + "..."

def _port_id_from_text(text: str, name_attr: str) -> str:
    # Tenta pegar número e ano (ex: 499/2025)
    m = re.search(r"PORTARIA\s+(?:GM/|MF\s+)?(?:MPO|MF)?\s*N[ºo]?\s*(\d+).+?(20\d{2})", text, flags=re.I)
    if m: return f"{m.group(1)}/{m.group(2)}"
    
    # Fallback no atributo do XML
    m2 = re.search(r"n\S*\s+(\d+)[\.\-_/](\d{4})", (name_attr or ""), flags=re.I)
    if m2: return f"{m2.group(1)}/{m2.group(2)}"
    
    return "N/D"

def _group_files_by_base(zip_names: Iterable[str]) -> Dict[str, List[Tuple[int, str]]]:
    groups = defaultdict(list)
    for n in zip_names:
        m = re.search(r"(\d+)(?:-(\d+))?\.xml$", n, flags=re.I)
        if m:
            base = m.group(1)
            suffix = int(m.group(2) or 0)
            groups[base].append((suffix, n))
    for base in groups: groups[base].sort()
    return groups

def _clean_brl(val_str: str) -> float:
    try:
        return float(val_str.replace(".", "").replace(",", "."))
    except:
        return 0.0

def _parse_totals_rows(xml_bytes: bytes, mb_ugs: Iterable[str]) -> List[Dict]:
    try:
        parser = ET.XMLParser(encoding="utf-8")
        art = ET.fromstring(xml_bytes, parser=parser)
    except: return []

    texto = art.find(".//body/Texto")
    if texto is None or texto.text is None: return []

    # Parse HTML
    try:
        clean_html = _sanitize_html_content(texto.text)
        root = ET.fromstring(f"<root>{clean_html}</root>")
    except: return []

    rows = []
    
    # Contexto Atual (State Machine)
    current_ug = None
    current_action = None # Código da ação (ex: 123H)
    current_kind = "OUTROS"
    current_rp_context = None # RP2, RP3, PAC
    
    mb_ugs = set(mb_ugs)

    # Itera sobre elementos (p e tr)
    for elem in root.iter():
        elem_text = " ".join(x.strip() for x in elem.itertext() if x.strip()).upper()
        
        # 1. Detectar Contexto Geral (Suplementação vs Cancelamento)
        if "REDUÇÃO" in elem_text or "CANCELAMENTO" in elem_text or "BLOQUEIO" in elem_text:
            current_kind = "CANCELAMENTO"
            current_action = None # Reseta ação ao mudar de bloco
        elif "ACRÉSCIMO" in elem_text or "SUPLEMENTA" in elem_text or "AMPLIAÇÃO" in elem_text:
            current_kind = "SUPLEMENTACAO"
            current_action = None

        # 2. Detectar Contexto de RP / Anexo (Portaria Limites/MF)
        if "RP 2" in elem_text or "PRIMÁRIAS DISCRICIONÁRIAS" in elem_text:
            current_rp_context = "RP2"
        elif "RP 3" in elem_text or "PAC" in elem_text:
            current_rp_context = "RP3 (PAC)"
        elif "RP 6" in elem_text or "RP 7" in elem_text:
            current_rp_context = "Emendas"
        elif "ANEXO II" in elem_text and "PAC" in elem_text:
            current_rp_context = "PAC (Anexo)"
            
        # 3. Processar Linhas de Tabela
        if elem.tag == 'tr':
            tr_text = " ".join(x.strip() for x in elem.itertext() if x.strip())
            tr_upper = tr_text.upper()

            # A) Detectar UG no Cabeçalho
            m_ug_header = re.search(r"UNIDADE:?\s*(\d{5})", tr_text, re.I)
            if m_ug_header:
                current_ug = m_ug_header.group(1)
                current_action = None # Nova UG, reseta ação
                continue

            # B) Detectar Programa de Trabalho (PT) -> Extrair Ação
            # Ex: 10.302.2015.8585.0000
            m_pt = re.search(r"\d{4}\.\d{4}\.\d{4}\.([0-9A-Z]{4})", tr_text)
            if m_pt:
                current_action = m_pt.group(1) # Ex: 8585
            
            # C) Detectar UG na linha (Tabelas de Limites/Financeiro)
            row_ug = current_ug
            m_ug_inline = re.search(r"^(\d{5})\b", tr_text.strip())
            if m_ug_inline:
                row_ug = m_ug_inline.group(1)

            # D) Se a linha pertence a uma UG de interesse
            if row_ug in mb_ugs:
                # Extrai valores
                matches = re.findall(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)", tr_text)
                if matches:
                    # Pega o maior valor da linha (geralmente é o total ou o valor alvo)
                    # Evita pegar "2025" (ano)
                    valid_vals = []
                    for v_str in matches:
                        v_float = _clean_brl(v_str)
                        if v_float > 2030: # Filtra ano
                            valid_vals.append(v_float)
                    
                    if valid_vals:
                        val = valid_vals[-1] # Assume o último como valor
                        
                        # Refinamento de RP na linha (se houver coluna explicita)
                        row_rp = current_rp_context
                        if "RP 2" in tr_upper: row_rp = "RP2"
                        if "RP 3" in tr_upper or "PAC" in tr_upper: row_rp = "RP3 (PAC)"

                        rows.append({
                            "UG": row_ug,
                            "kind": current_kind,
                            "action": current_action, # Pode ser None
                            "rp": row_rp, # Pode ser None
                            "valor": val
                        })
                        
                        # Reseta UG inline para não contaminar próximas linhas se não for tabela contínua
                        if m_ug_inline: current_ug = None 

    return rows

def _brl(n: float) -> str:
    s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

# ---------------- API PÚBLICA ---------------- #

def parse_zip_in_memory(zip_file_obj: Union[str, io.BytesIO], mb_ugs: Iterable[str] = None):
    if mb_ugs is None: mb_ugs = MB_UGS_DEFAULT
    try: z = zipfile.ZipFile(zip_file_obj, "r")
    except zipfile.BadZipFile: return {}, {}

    agg = defaultdict(list)
    pid_to_hint = {}

    with z:
        xml_names = [n for n in z.namelist() if n.lower().endswith(".xml")]
        groups = _group_files_by_base(xml_names)
        base_to_pid = {}
        base_to_hint = {}

        for base, items in groups.items():
            header_name = items[0][1] 
            try:
                with z.open(header_name) as f: 
                    xmlb = f.read()
                    parser = ET.XMLParser(encoding="utf-8")
                    art = ET.fromstring(xmlb, parser=parser)
                    
                    text_node = art.find(".//body/Texto")
                    full_text = _html_to_text(text_node.text) if text_node else ""
                    
                    # Filtro de Relevância
                    cat = art.attrib.get("artCategory", "").upper()
                    is_budget = "MPO" in cat or "PLANEJAMENTO" in cat or "FAZENDA" in cat
                    is_budget_text = "MPO" in full_text or "ORÇAMENTO" in full_text
                    
                    pid = _port_id_from_text(full_text, art.attrib.get("name", ""))
                    
                    if is_budget or is_budget_text:
                        base_to_pid[base] = pid
                        base_to_hint[base] = _extract_header_hint(full_text)
                    
            except: continue

        for n in xml_names:
            m = re.search(r"(\d+)(?:-(\d+))?\.xml$", n)
            if not m: continue
            base = m.group(1)
            if base in base_to_pid:
                pid = base_to_pid[base]
                try:
                    with z.open(n) as f:
                        rows = _parse_totals_rows(f.read(), mb_ugs)
                        if rows:
                            agg[pid].extend(rows)
                except: continue
                
        for base, pid in base_to_pid.items():
            if pid in agg:
                pid_to_hint[pid] = base_to_hint.get(base, "Ato Orçamentário")

    return agg, pid_to_hint

def render_whatsapp_block(pid: str, hint: str, rows: List[Dict]) -> str:
    """Renderiza a mensagem no formato desejado pelo usuário (Granularidade de Ação)."""
    
    # 1. Separa tipos
    sup_rows = [r for r in rows if r["kind"] == "SUPLEMENTACAO"]
    canc_rows = [r for r in rows if r["kind"] == "CANCELAMENTO"]
    
    wa = []
    wa.append(f"🔎 *Análise Contábil Automática ({pid})*")
    wa.append(f"_{hint}_")
    wa.append("")

    def render_section(title, emoji, data_rows):
        if not data_rows: return 0.0
        
        # Agrupa por UG + Ação + RP
        grouped = defaultdict(float)
        for r in data_rows:
            key_ug = r["UG"]
            key_act = r["action"] if r["action"] else "Geral"
            key_rp = r["rp"] if r["rp"] else ""
            grouped[(key_ug, key_act, key_rp)] += r["valor"]
            
        total_sec = sum(grouped.values())
        wa.append(f"{emoji} *{title}:* {_brl(total_sec)}")
        
        # Ordena: Defesa (52000) e CM (52111/52131) primeiro
        sorted_keys = sorted(grouped.keys(), key=lambda k: k[0])
        
        for ug, act, rp in sorted_keys:
            # Formata Nome da Ação
            act_name = act
            if act in STRATEGIC_MAP:
                act_name = f"{act} ({STRATEGIC_MAP[act]})"
            elif act == "Geral":
                act_name = "" # Não mostra 'Geral' se não tiver ação
            else:
                act_name = f"Ação {act}"

            # Formata RP/Contexto
            rp_str = f" [{rp}]" if rp else ""
            
            # Formata UG
            ug_sufix = ""
            if ug == "52000": ug_sufix = "- MD"
            elif ug == "52131": ug_sufix = "- CM"
            elif ug == "52931": ug_sufix = "- FuN"
            
            line_str = f"   └ UG {ug}{ug_sufix}"
            if act_name: line_str += f" | {act_name}"
            if rp_str: line_str += rp_str
            line_str += f": {_brl(grouped[(ug, act, rp)])}"
            
            wa.append(line_str)
        
        return total_sec

    tot_sup = render_section("Ampliação/Suplementação", "✅", sup_rows)
    if tot_sup > 0 and canc_rows: wa.append("") # Espaçamento
    tot_canc = render_section("Redução/Cancelamento", "🔻", canc_rows) # Usei 🔻 pois é mais visual que vermelho sólido

    net = tot_sup - tot_canc
    wa.append("")
    
    if net > 0:
        wa.append(f"💰 *Saldo Líquido Positivo:* {_brl(net)}")
    elif net < 0:
        wa.append(f"⚠️ *Saldo Líquido Negativo:* {_brl(net)}")
    else:
        wa.append(f"⚪ *Remanejamento sem alteração líquida.*")

    return "\n".join(wa)
