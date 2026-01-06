document.addEventListener("DOMContentLoaded", function() {

  // 👉 Troque para a URL do seu backend no Render se necessário, ou deixe vazio para relativo:
  const API_BASE = "";

  const el = (id) => document.getElementById(id);
  const btnProcessar = el("btnProcessar");
  const btnProcessarIA = el("btnProcessarIA");
  const btnProcessarValor = el("btnProcessarValor");
  // Removido: const btnTesteFallback... (pois agora é um link externo)
  const btnCopiar = el("btnCopiar");
  const preview = el("preview");

  // Valor padrão: hoje
  (function initDate() {
    const today = new Date().toISOString().slice(0, 10);
    if (el("data")) {
      el("data").value = today;
    }
  })();

  // Função central de processamento
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

    // Adiciona campos específicos do DOU
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
    
    // Texto específico do Valor
    if (endpoint.startsWith("/processar-valor")) {
        loadingText = "Buscando notícias no Valor Econômico e analisando com IA, aguarde…";
    }

    // Desabilita botões durante o processamento
    if (btnProcessar) btnProcessar.disabled = true;
    if (btnProcessarIA) btnProcessarIA.disabled = true;
    if (btnProcessarValor) btnProcessarValor.disabled = true;
    if (btnCopiar) btnCopiar.disabled = true;

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
      
      if (btnProcessar) btnProcessar.disabled = false;
      if (btnProcessarIA) btnProcessarIA.disabled = false;
      if (btnProcessarValor) btnProcessarValor.disabled = false;
      if (preview) preview.classList.remove("loading");
    }
  }

  // Listeners dos botões
  if (btnProcessar) {
    btnProcessar.addEventListener("click", () => handleProcessing("/processar-inlabs"));
  }
  if (btnProcessarIA) {
    btnProcessarIA.addEventListener("click", () => handleProcessing("/processar-dou-ia"));
  }
  if (btnProcessarValor) {
    btnProcessarValor.addEventListener("click", () => handleProcessing("/processar-valor-ia"));
  }
  
  // O Listener do btnTesteFallback foi removido pois agora é um link HTML padrão.

  // Botão Copiar (Atualizado com Feedback Visual)
  if (btnCopiar) {
    btnCopiar.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(preview.textContent || "");
        
        // Feedback Visual: Pisca a caixa em verde
        preview.style.backgroundColor = "#d4edda"; 
        preview.style.transition = "background-color 0.2s";
        setTimeout(() => {
            preview.style.backgroundColor = ""; 
        }, 300);

        // Feedback no Botão
        btnCopiar.textContent = "Copiado!";
        setTimeout(() => (btnCopiar.textContent = "Copiar Relatório"), 1200);
      } catch (err) {
        alert("Falha ao copiar para a área de transferência.");
      }
    });
  }

});
