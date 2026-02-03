document.addEventListener("DOMContentLoaded", function() {

  // 👉 Troque para a URL do seu backend no Render se necessário, ou deixe vazio para relativo:
  const API_BASE = "";

  // Função auxiliar para pegar elementos pelo ID
  const el = (id) => document.getElementById(id);
  
  // --- 1. Mapeamento dos Elementos ---
  const btnProcessar = el("btnProcessar");
  const btnProcessarIA = el("btnProcessarIA");
  const btnProcessarValor = el("btnProcessarValor");
  const btnBaixarPdf = el("btnBaixarPdf");
  const btnCopiar = el("btnCopiar");
  
  // Inputs e Áreas de Texto
  const dateInput = el("data");
  const sectionsInput = el("sections");
  const keywordsInput = el("keywords");
  const preview = el("preview");

  // --- 2. Inicialização (Define data de hoje) ---
  (function initDate() {
    const today = new Date().toISOString().slice(0, 10);
    if (dateInput) {
      dateInput.value = today;
    }
  })();

  // --- 3. Lógica do Botão BAIXAR PDF (InLabs) ---
  if (btnBaixarPdf) {
    btnBaixarPdf.addEventListener('click', (e) => {
        e.preventDefault(); // Impede que o link recarregue a página

        const dateVal = dateInput ? dateInput.value : null;
        
        if (!dateVal) {
            alert('Por favor, selecione uma data primeiro.');
            return;
        }

        // Feedback Visual
        const originalText = btnBaixarPdf.innerText;
        btnBaixarPdf.innerText = "⏳ Baixando do InLabs...";
        btnBaixarPdf.style.opacity = "0.7";
        btnBaixarPdf.style.pointerEvents = "none"; // Bloqueia clique duplo

        // Chama a rota do Backend (Proxy Direto)
        // Isso vai acionar o download do arquivo 'YYYY_MM_DD_ASSINADO_do1.pdf'
        window.location.href = `/download-pdf-inlabs?date=${dateVal}&section=do1`;

        // Restaura o botão após 5 segundos (tempo estimado para o download iniciar)
        setTimeout(() => {
            btnBaixarPdf.innerText = originalText;
            btnBaixarPdf.style.opacity = "1";
            btnBaixarPdf.style.pointerEvents = "auto";
        }, 5000);
    });
  }

  // --- 4. Função Central de Processamento (Backend) ---
  async function handleProcessing(endpoint) {
    if (!dateInput || !dateInput.value) {
      if(preview) preview.textContent = "Informe a data (YYYY-MM-DD).";
      alert("Informe a data!");
      return;
    }

    const dataVal = dateInput.value.trim();
    const sectionsVal = sectionsInput ? sectionsInput.value.trim() : "DO1,DO2";
    const keywordsVal = keywordsInput ? keywordsInput.value.trim() : "";

    // UI: Prepara para carregar
    let loadingText = "Processando, aguarde…";
    
    // Personaliza mensagem de carregamento
    if(endpoint.includes("processar-dou-ia")) {
       loadingText = "🤖 Processando DOU com IA no INLABS. Isso pode levar até 2 minutos...";
    } else if(endpoint.includes("processar-valor")) {
       loadingText = "📰 Buscando notícias no Valor Econômico e analisando com IA...";
    } else {
       loadingText = "🔎 Processando DOU (Rápido) no INLABS...";
    }

    if (preview) {
      preview.classList.add("loading");
      preview.textContent = loadingText;
    }

    // Desabilita botões para evitar cliques múltiplos
    toggleButtons(true);

    try {
      const fd = new FormData();
      fd.append("data", dataVal);

      // Lógica específica para endpoints do DOU
      if (endpoint.includes("processar-dou") || endpoint.includes("processar-inlabs")) {
        fd.append("sections", sectionsVal);
        
        if (keywordsVal) {
          const keywordsList = keywordsVal.split(',')
            .map(k => k.trim())
            .filter(k => k.length > 0);
            
          if (keywordsList.length > 0) {
            fd.append("keywords_json", JSON.stringify(keywordsList));
          }
        }
      }

      // Envia a requisição
      const res = await fetch(`${API_BASE}${endpoint}`, { method: "POST", body: fd }); 
      const body = await res.json().catch(() => ({}));

      if (!res.ok) {
        throw new Error(body.detail || `Erro HTTP ${res.status}`);
      }

      const texto = body.whatsapp_text || "(Sem resultados relevantes encontrados para os filtros aplicados)";
      
      if (preview) {
          preview.textContent = texto;
          // Se quiser renderizar HTML (negrito, quebras de linha reais):
          // preview.innerHTML = texto.replace(/\n/g, "<br>"); 
      }

      // Habilita botão de copiar se houver texto
      if (btnCopiar) {
        btnCopiar.disabled = !texto || texto === "(Sem resultados)";
      }

    } catch (err) {
      if (preview) preview.textContent = `Falha na requisição: ${err.message || err}`;
      console.error(err);
    } finally {
      // Reabilita botões e remove loading
      toggleButtons(false);
      if (preview) preview.classList.remove("loading");
    }
  }

  // Função auxiliar para travar/destravar botões
  function toggleButtons(disabled) {
    if (btnProcessar) btnProcessar.disabled = disabled;
    if (btnProcessarIA) btnProcessarIA.disabled = disabled;
    if (btnProcessarValor) btnProcessarValor.disabled = disabled;
    if (btnCopiar) btnCopiar.disabled = disabled;
  }

  // --- 5. Listeners dos Botões de Ação ---
  if (btnProcessar) {
    btnProcessar.addEventListener("click", () => handleProcessing("/processar-inlabs"));
  }
  if (btnProcessarIA) {
    btnProcessarIA.addEventListener("click", () => handleProcessing("/processar-dou-ia"));
  }
  if (btnProcessarValor) {
    btnProcessarValor.addEventListener("click", () => handleProcessing("/processar-valor-ia"));
  }
  
  // --- 6. Botão Copiar (Com feedback visual) ---
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
