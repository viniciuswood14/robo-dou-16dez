document.addEventListener("DOMContentLoaded", function() {

  // 👉 Troque para a URL do seu backend no Render se necessário, ou deixe vazio para relativo:
  const API_BASE = "";

  const el = (id) => document.getElementById(id);
  
  // Elementos (Botões)
  const btnProcessar = el("btnProcessar");
  const btnProcessarIA = el("btnProcessarIA");
  const btnProcessarValor = el("btnProcessarValor");
  const btnBaixarPdf = el("btnBaixarPdf"); // Novo botão para o PDF
  const btnCopiar = el("btnCopiar");
  
  // Elementos (Inputs/Output)
  const preview = el("preview");

  // Valor padrão: hoje
  (function initDate() {
    const today = new Date().toISOString().slice(0, 10);
    if (el("data")) {
      el("data").value = today;
    }
  })();

  // --- 1. Lógica do Botão PDF (Site Oficial) ---
  if (btnBaixarPdf) {
    btnBaixarPdf.addEventListener("click", () => {
      const dataVal = el("data").value; // Vem como YYYY-MM-DD
      
      if (!dataVal) {
        alert("Por favor, informe a data (YYYY-MM-DD) antes de abrir o PDF.");
        return;
      }

      // Validação e conversão de formato
      const parts = dataVal.split("-");
      if (parts.length !== 3) {
        alert("Data inválida.");
        return;
      }
      
      const [ano, mes, dia] = parts;
      // O site do governo usa DD-MM-YYYY
      const dataFormatada = `${dia}-${mes}-${ano}`;

      // Abre a URL oficial da Seção 1 em nova aba
      const url = `https://www.in.gov.br/leitura-jornal?data=${dataFormatada}&secao=do1`;
      window.open(url, "_blank");
    });
  }

  // --- 2. Função central de processamento (Backend) ---
  async function handleProcessing(endpoint) {
    const data = el("data").value.trim();
    const sections = el("sections").value.trim() || "DO1,DO2";
    const keywords = el("keywords").value.trim();

    if (!data) {
      if(preview) preview.textContent = "Informe a data (YYYY-MM-DD).";
      return;
    }

    const fd = new FormData();
    fd.append("data", data);
    
    let loadingText = "Processando, aguarde…";

    // Configuração para rotas do DOU
    if (endpoint.startsWith("/processar-dou") || 
        endpoint.startsWith("/processar-inlabs")) {
      
      fd.append("sections", sections);
      
      if (keywords) {
        const keywordsList = keywords.split(',')
          .map(k => k.trim())
          .filter(k => k.length > 0);
          
        if (keywordsList.length > 0) {
          fd.append("keywords_json", JSON.stringify(keywordsList));
        }
      }
      
      if(endpoint.includes("-ia")) {
        loadingText = "Processando DOU com IA no INLABS. Isso pode levar até 2 minutos, aguarde…";
      } else {
        loadingText = "Processando DOU (Rápido) no INLABS, aguarde…";
      }
    }
    
    // Configuração para rota do Valor
    if (endpoint.startsWith("/processar-valor")) {
        loadingText = "Buscando notícias no Valor Econômico e analisando com IA, aguarde…";
    }

    // Desabilita botões que dependem do backend durante o processamento
    if (btnProcessar) btnProcessar.disabled = true;
    if (btnProcessarIA) btnProcessarIA.disabled = true;
    if (btnProcessarValor) btnProcessarValor.disabled = true;
    if (btnCopiar) btnCopiar.disabled = true;
    // Nota: O btnBaixarPdf não é desabilitado pois não depende do backend

    if (preview) {
      preview.classList.add("loading");
      preview.textContent = loadingText;
    }

    try {
      const res = await fetch(`${API_BASE}${endpoint}`, { method: "POST", body: fd }); 
      const body = await res.json().catch(() => ({}));

      if (!res.ok) {
        if(preview) preview.textContent = body?.detail
          ? `Erro: ${body.detail}`
          : `Erro HTTP ${res.status}`;
        return;
      }

      const texto = body?.whatsapp_text || "(Sem resultados)";
      if (preview) preview.textContent = texto;

      if (btnCopiar) {
        btnCopiar.disabled = !texto || texto === "(Sem resultados)";
      }

    } catch (err) {
      if (preview) preview.textContent = `Falha na requisição: ${err.message || err}`;
    } finally {
      // Reabilita botões
      if (btnProcessar) btnProcessar.disabled = false;
      if (btnProcessarIA) btnProcessarIA.disabled = false;
      if (btnProcessarValor) btnProcessarValor.disabled = false;
      if (preview) preview.classList.remove("loading");
    }
  }

  // --- 3. Listeners dos botões de processamento ---
  if (btnProcessar) {
    btnProcessar.addEventListener("click", () => handleProcessing("/processar-inlabs"));
  }
  if (btnProcessarIA) {
    btnProcessarIA.addEventListener("click", () => handleProcessing("/processar-dou-ia"));
  }
  if (btnProcessarValor) {
    btnProcessarValor.addEventListener("click", () => handleProcessing("/processar-valor-ia"));
  }
  
  // --- 4. Botão Copiar (Com feedback visual) ---
  if (btnCopiar) {
    btnCopiar.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(preview.textContent || "");
        
        // Feedback Visual na caixa de texto (pisca verde)
        preview.style.backgroundColor = "#d4edda"; 
        preview.style.transition = "background-color 0.2s";
        setTimeout(() => {
            preview.style.backgroundColor = ""; 
        }, 300);

        // Feedback no Texto do Botão
        const originalText = btnCopiar.textContent;
        btnCopiar.textContent = "Copiado!";
        setTimeout(() => (btnCopiar.textContent = originalText), 1200);
      } catch (err) {
        alert("Falha ao copiar para a área de transferência.");
      }
    });
  }

});
