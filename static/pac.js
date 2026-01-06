document.addEventListener("DOMContentLoaded", function() {

  // 👉 Aponte para a URL do seu backend no Render:
  const API_BASE = "";

  const el = (id) => document.getElementById(id);
  const btnConsultar = el("btnConsultarPAC");
  const inputAno = el("ano");
  const tableHeader = el("table-header");
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
    return "R$ " + value.toLocaleString('pt-BR', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  function formatPercent(value) {
    if (typeof value !== 'number' || isNaN(value)) return "0,0%";
    return (value * 100).toLocaleString('pt-BR', {
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
    
    inputAno.value = ano;

    btnConsultar.disabled = true;
    loadingText.textContent = `Consultando SIOP para ${ano}, aguarde...`;
    loadingText.style.display = "block";
    errorText.style.display = "none";
    tableContainer.style.display = "none";

    try {
      const response = await fetch(`${API_BASE}/api/pac-data/${ano}`);
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || `Erro HTTP ${response.status}`);
      }

      // 1. Limpa a tabela antiga
      tableHeader.innerHTML = "";
      tableBody.innerHTML = "";

      // 2. Define os cabeçalhos
      // A última coluna foi renomeada para refletir a nova fórmula
      const headersDisplay = [
        'PROGRAMA', 'AÇÃO', 'LOA', 'DOTAÇÃO ATUAL', 
        'DISPONÍVEL',  
        'EMPENHADO (c)', 'LIQUIDADO', 'PAGO', 
        'IND. (E-D)/E'
      ];
      
      headersDisplay.forEach(headerText => {
        const th = document.createElement("th");
        th.textContent = headerText;
        if (headerText !== 'PROGRAMA' && headerText !== 'AÇÃO') {
            th.classList.add("num");
        }
        tableHeader.appendChild(th);
      });

      // 3. Preenche as linhas da tabela
      data.forEach(rowData => {
        const tr = document.createElement("tr");

        if (rowData.PROGRAMA === 'Total Geral') tr.classList.add("row-total");
        else if (rowData.AÇÃO === null) tr.classList.add("row-programa");
        else tr.classList.add("row-acao");

        // Valores vindos da API
        const dotacao = rowData['DOTAÇÃO ATUAL'] || 0;
        const empenhado = rowData['EMPENHADO (c)'] || 0;
        // Agora lemos o disponível direto da API, sem cálculo local
        const disponivel = rowData['DISPONÍVEL'] || 0; 

        // Cálculo da métrica solicitada: (Empenhado - Disponível) / Empenhado
        let novoIndicador = 0;
        if (empenhado !== 0) {
            novoIndicador = (empenhado - disponivel) / empenhado;
        }

        // Monta o array na ordem das colunas
        const valoresOrdenados = [
            rowData['PROGRAMA'],
            rowData['AÇÃO'],
            rowData['LOA'],
            dotacao,
            disponivel, // Valor do SIOP
            empenhado,
            rowData['LIQUIDADO'],
            rowData['PAGO'],
            novoIndicador // Valor calculado
        ];

        valoresOrdenados.forEach((value, index) => {
            const td = document.createElement("td");
            const headerName = headersDisplay[index];

            if (headerName === 'PROGRAMA' || headerName === 'AÇÃO') {
                td.textContent = value || "";
            } 
            else if (headerName === 'IND. (E-D)/E') {
                td.textContent = formatPercent(value);
                td.classList.add("num");
                
                // Destaque visual se negativo (opcional)
                if (value < 0) {
                    td.style.color = "#d9534f"; // Vermelho suave
                    td.style.fontWeight = "bold";
                }
            } 
            else {
                td.textContent = formatCurrency(value);
                td.classList.add("num");
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

  // --- Função do Gráfico (Mantida igual) ---
  async function fetchAndRenderChart() {
    loadingText.textContent = "Carregando gráfico histórico (2010-2025)...";
    loadingText.style.display = "block";
    errorText.style.display = "none";
    tableContainer.style.display = "none";
    
    try {
      const response = await fetch(`${API_BASE}/api/pac-data/historical-dotacao`);
      const chartData = await response.json(); 

      if (!response.ok) throw new Error(chartData.detail || `Erro HTTP ${response.status}`);

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
            title: { display: true, text: 'Dotação Autorizada (LOA + Créditos) por Ação e Ano', font: { size: 16, weight: '600' }, color: '#002c5f' },
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
            y: { stacked: true, title: { display: true, text: 'Dotação (R$)' }, ticks: { callback: function(value) { return formatCurrency(value); } } }
          },
          onClick: (e) => {
            const activePoints = pacChart.getElementsAtEventForMode(e, 'index', { intersect: true }, true);
            if (activePoints.length > 0) {
                const clickedIndex = activePoints[0].index;
                const clickedYear = chartData.labels[clickedIndex];
                fetchAndRenderTable(clickedYear);
            }
          }
        }
      });
      
      const ultimoAno = chartData.labels[chartData.labels.length - 1];
      fetchAndRenderTable(ultimoAno);

    } catch (err) {
        if (err.message.includes("404")) {
             errorText.textContent = "O robô ainda está compilando os dados históricos. Aguarde ou consulte a tabela abaixo.";
        } else {
             errorText.textContent = `Erro ao carregar o gráfico: ${err.message}`;
        }
        errorText.style.display = "block";
        loadingText.style.display = "none";
        fetchAndRenderTable(new Date().getFullYear());
    }
  }

  btnConsultar.addEventListener("click", () => fetchAndRenderTable(inputAno.value));
  fetchAndRenderChart();
});
