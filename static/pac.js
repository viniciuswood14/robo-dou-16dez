document.addEventListener("DOMContentLoaded", function() {

  // 👉 Aponte para a URL do seu backend no Render se necessário:
  const API_BASE = "";

  const el = (id) => document.getElementById(id);
  const btnConsultar = el("btnConsultarPAC");
  const inputAno = el("ano");
  const tableBody = el("table-body");
  const tableContainer = el("table-container");
  const loadingText = el("loading-text");
  const errorText = el("error-text");
  const chartCanvas = el("pacChart");

  let pacChart = null; 

  // Define o ano padrão para o ano atual
  (function initYear() {
    if(inputAno) {
        inputAno.value = new Date().getFullYear();
    }
  })();

  // --- Helpers de Formatação ---
  function formatCurrency(value) {
    if (typeof value !== 'number') return "R$ 0,00";
    return value.toLocaleString('pt-BR', {
      style: 'currency',
      currency: 'BRL',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  function formatPercent(value) {
    if (typeof value !== 'number' || isNaN(value)) return "0,0%";
    return value.toLocaleString('pt-BR', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1
    }) + "%";
  }

  // --- Função da Tabela (chamada por Ano) ---
  async function fetchAndRenderTable(ano) {
    if (!ano) {
      errorText.textContent = "Ano inválido selecionado.";
      errorText.style.display = "block";
      tableContainer.style.display = "none";
      return;
    }
    
    // Atualiza o input se a chamada vier do clique no gráfico
    if(inputAno.value !== ano) inputAno.value = ano;

    btnConsultar.disabled = true;
    loadingText.textContent = `Consultando SIOP para ${ano} (Calculando P+D)... aguarde...`;
    loadingText.style.display = "block";
    errorText.style.display = "none";
    tableContainer.style.display = "none"; // Esconde tabela antiga enquanto carrega

    try {
      const response = await fetch(`${API_BASE}/api/pac-data/${ano}`);
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || `Erro HTTP ${response.status}`);
      }

      // 1. Limpa o corpo da tabela
      tableBody.innerHTML = "";

      // 2. Preenche as linhas
      // A ordem das chaves deve bater com o <thead> do HTML
      const keys = [
          'PROGRAMA', 
          'AÇÃO', 
          'DOTAÇÃO ATUAL', 
          'PROVISIONADO (P)', 
          'DESTAQUE (D)', 
          'P+D', 
          'EMPENHADO', 
          'PAGO', 
          '% EXEC (P+D)'
      ];

      data.forEach(rowData => {
        const tr = document.createElement("tr");

        // Define classes CSS para estilização (Total vs Programa vs Ação)
        if (rowData.PROGRAMA === 'Total Geral') {
            tr.classList.add("row-total");
        } else if (rowData.AÇÃO === null) {
            tr.classList.add("row-programa");
        } else {
            tr.classList.add("row-acao");
        }

        keys.forEach((key) => {
            const td = document.createElement("td");
            const val = rowData[key];

            if (key === 'PROGRAMA' || key === 'AÇÃO') {
                td.textContent = val || "";
            } 
            else if (key === '% EXEC (P+D)') {
                td.textContent = formatPercent(val);
                td.style.fontWeight = "bold";
                // Destaque visual: se execução for muito baixa (<10%) e não for linha de título
                if (val < 10 && rowData.AÇÃO !== null) {
                     td.style.color = "#d9534f"; // Vermelho
                } else if (val > 90) {
                     td.style.color = "#28a745"; // Verde
                }
            } 
            else {
                // Colunas numéricas monetárias
                td.textContent = formatCurrency(val);
            }
            tr.appendChild(td);
        });

        tableBody.appendChild(tr);
      });

      tableContainer.style.display = "block"; 

    } catch (err) {
      errorText.textContent = `Erro ao consultar ${ano}: ${err.message}`;
      errorText.style.display = "block";
    } finally {
      btnConsultar.disabled = false;
      loadingText.style.display = "none";
    }
  }

  // --- Função do Gráfico (Histórico) ---
  async function fetchAndRenderChart() {
    loadingText.textContent = "Carregando gráfico histórico...";
    loadingText.style.display = "block";
    errorText.style.display = "none";
    
    try {
      const response = await fetch(`${API_BASE}/api/pac-data/historical-dotacao`);
      const chartData = await response.json(); 

      if (!response.ok) throw new Error(chartData.detail || `Erro HTTP ${response.status}`);

      // Paleta de cores da Marinha/Defesa
      const colors = ['rgba(0, 44, 95, 0.8)', 'rgba(0, 95, 86, 0.8)', 'rgba(255, 184, 28, 0.8)', 'rgba(60, 120, 216, 0.8)', 'rgba(217, 83, 79, 0.8)'];
      
      chartData.datasets.forEach((dataset, index) => {
          dataset.backgroundColor = colors[index % colors.length];
      });

      const ctx = chartCanvas.getContext('2d');
      pacChart = new Chart(ctx, {
        type: 'bar',
        data: chartData,
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            title: { 
                display: true, 
                text: 'Histórico: Dotação Autorizada (LOA + Créditos)', 
                font: { size: 16, weight: '600' }, 
                color: '#002c5f' 
            },
            legend: { position: 'top' },
            tooltip: {
                callbacks: {
                    label: function(context) {
                        let label = context.dataset.label || '';
                        if (label) label += ': ';
                        if (context.parsed.y !== null) label += formatCurrency(context.parsed.y);
                        return label;
                    }
                }
            }
          },
          scales: {
            x: { stacked: true, title: { display: true, text: 'Exercício (Ano)' } },
            y: { 
                stacked: true, 
                title: { display: true, text: 'Dotação (R$)' }, 
                ticks: { callback: function(value) { return formatCurrency(value); } } 
            }
          },
          // Evento de Clique nas Barras
          onClick: (e) => {
            const activePoints = pacChart.getElementsAtEventForMode(e, 'index', { intersect: true }, true);
            if (activePoints.length > 0) {
                const clickedIndex = activePoints[0].index;
                const clickedYear = chartData.labels[clickedIndex];
                // Carrega a tabela do ano clicado
                fetchAndRenderTable(clickedYear);
            }
          }
        }
      });
      
      // Carrega automaticamente a tabela do último ano disponível no gráfico
      if (chartData.labels && chartData.labels.length > 0) {
          const ultimoAno = chartData.labels[chartData.labels.length - 1];
          fetchAndRenderTable(ultimoAno);
      } else {
          // Fallback se o gráfico estiver vazio
          fetchAndRenderTable(new Date().getFullYear());
      }

    } catch (err) {
        if (err.message.includes("404")) {
             errorText.textContent = "Cache histórico ainda não gerado. Consulte a tabela abaixo manualmente.";
        } else {
             errorText.textContent = `Erro ao carregar gráfico: ${err.message}`;
        }
        errorText.style.display = "block";
        loadingText.style.display = "none";
        // Tenta carregar a tabela mesmo sem gráfico
        fetchAndRenderTable(new Date().getFullYear());
    }
  }

  // Listeners
  btnConsultar.addEventListener("click", () => fetchAndRenderTable(inputAno.value));
  
  // Inicialização
  fetchAndRenderChart();
});
