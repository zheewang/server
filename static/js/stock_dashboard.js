import {
    initPagination,
    updatePagination,
    renderPagination,
    changePage,
    bindPerPageInput,
    sortData,
    updateSortIndicators,
    bindSortEvents,
    makeTableSortable,
} from './utils.js';

let pagination = initPagination();
let stockData = [];
let filteredData = [];
let sortRules = [];
let recentDates = [];
let sectors = [];

const BASE_URL = `http://${HOST}:${PORT}`;
const PAGE_KEY = 'stock_dashboard';

document.addEventListener('DOMContentLoaded', function() {
    fetch(`${BASE_URL}/api/sectors`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            sectors = data;

            const thsSelect = document.getElementById('th_sector_index');
            const uniqueTHS = [...new Set(sectors.map(s => s.THSSectorIndex))];
            uniqueTHS.forEach(ths => {
                const option = document.createElement('option');
                option.value = ths;
                option.textContent = ths;
                thsSelect.appendChild(option);
            });

            $('#sector_name').select2({
                placeholder: 'Search a sector',
                allowClear: true
            });

            const savedState = JSON.parse(sessionStorage.getItem(`${PAGE_KEY}_state`));
            if (savedState) {
                pagination.currentPage = savedState.currentPage || 1;
                pagination.perPage = savedState.perPage || 30;
                stockData = savedState.stockData || [];
                filteredData = savedState.filteredData || [...stockData]; // 明确初始化
                sortRules = savedState.sortRules || [];
                document.getElementById('perPage').value = pagination.perPage;
                document.getElementById('date').value = savedState.date || '';
                document.getElementById('th_sector_index').value = savedState.thSectorIndex || '';
                document.getElementById('sector_name').value = savedState.sectorName || '';
                document.getElementById('sector_code').value = savedState.sectorCode || '';
                if (stockData.length > 0) {
                    updatePagination(pagination, filteredData.length);
                    updateTableHeaders();
                    renderTable();
                }
            }

            // 延迟绑定排序事件
            setTimeout(() => {
                makeTableSortable();
                bindSortEvents(filteredData, sortRules, renderTable, saveState);
                console.log('Sorting events bound');
            }, 0);
        })
        .catch(error => {
            console.error('Error fetching sectors:', error);
        });

    bindPerPageInput(pagination, filteredData, renderTable, saveState);

    document.getElementById('fetchDataBtn')?.addEventListener('click', fetchData);
    document.getElementById('prevPage')?.addEventListener('click', () => changePage(pagination, -1, renderTable));
    document.getElementById('nextPage')?.addEventListener('click', () => changePage(pagination, 1, renderTable));
    document.getElementById('search')?.addEventListener('input', applyFilters);

    document.getElementById('th_sector_index').addEventListener('change', function() {
        const selectedTHS = this.value;
        const sectorNameSelect = document.getElementById('sector_name');
        const sectorCodeInput = document.getElementById('sector_code');

        sectorNameSelect.innerHTML = '<option value="">Select a sector</option>';
        sectorCodeInput.value = '';

        if (selectedTHS) {
            const filteredSectors = sectors.filter(sector => sector.THSSectorIndex === selectedTHS);
            filteredSectors.forEach(sector => {
                const option = document.createElement('option');
                option.value = sector.SectorIndexCode;
                option.textContent = sector.SectorIndexName;
                option.dataset.code = sector.SectorIndexCode;
                sectorNameSelect.appendChild(option);
            });

            $('#sector_name').select2({
                placeholder: 'Search a sector',
                allowClear: true
            });
            saveState();
        }
    });

    $('#sector_name').on('change', function() {
        const selectedCode = $(this).val();
        const sectorCodeInput = document.getElementById('sector_code');
        if (selectedCode) {
            sectorCodeInput.value = selectedCode;
        } else {
            sectorCodeInput.value = '';
        }
        saveState();
    });
});

function fetchData() {
    const date = document.getElementById('date').value;
    const sectorCode = document.getElementById('sector_code').value;
    pagination.perPage = parseInt(document.getElementById('perPage').value, 10) || 30;

    if (!date || !sectorCode) {
        alert('Please enter a date and specify a sector code');
        return;
    }

    const url = `${BASE_URL}/api/stock_data?date=${date}&sector_code=${sectorCode}`;
    console.log('Fetching data with URL:', url);

    fetch(url)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (!Array.isArray(data)) {
                throw new Error('Expected an array but received: ' + JSON.stringify(data));
            }

            stockData = data;
            filteredData = [...stockData];
            updateTableHeaders();
            updatePagination(pagination, filteredData.length);
            renderTable();
            saveState();
            // 重新绑定排序事件
            setTimeout(() => {
                makeTableSortable();
                bindSortEvents(filteredData, sortRules, renderTable, saveState);
            }, 0);
        })
        .catch(error => {
            console.error('Error fetching data:', error);
            stockData = [];
            filteredData = [];
            renderTable();
            saveState();
        });
}

function updateTableHeaders() {
    if (stockData.length > 0 && stockData[0].recent_data && stockData[0].recent_data.length > 0) {
        recentDates = stockData[0].recent_data.map(item => item.trading_Date).slice(0, 3);
        const thead = document.getElementById('tableHeader').querySelector('tr');
        while (thead.children.length > 8) {
            thead.removeChild(thead.lastChild);
        }
        recentDates.forEach((date, index) => {
            const th = document.createElement('th');
            th.textContent = date;
            th.dataset.sort = `recentChange${index}`;
            th.dataset.text = date;
            thead.appendChild(th);
        });
        // 重新绑定排序事件
        setTimeout(() => {
            bindSortEvents(filteredData, sortRules, renderTable, saveState);
        }, 0);
    } else {
        const thead = document.getElementById('tableHeader').querySelector('tr');
        while (thead.children.length > 8) {
            thead.removeChild(thead.lastChild);
        }
    }
}

function applyFilters() {
    const searchValue = document.getElementById('search').value.toLowerCase();
    filteredData = stockData.filter(stock =>
        stock.StockCode.toLowerCase().includes(searchValue) ||
        stock.StockName.toLowerCase().includes(searchValue)
    );
    updatePagination(pagination, filteredData.length);
    renderTable();
    saveState();
    // 重新绑定排序事件
    setTimeout(() => {
        makeTableSortable();
        bindSortEvents(filteredData, sortRules, renderTable, saveState);
    }, 0);
}

function renderTable() {
    const tbody = document.querySelector('#stockTable tbody');
    if (!tbody) {
        console.error('tbody not found in DOM');
        return;
    }
    tbody.innerHTML = '';

    const start = (pagination.currentPage - 1) * pagination.perPage;
    const end = Math.min(start + pagination.perPage, filteredData.length);
    const pageData = filteredData.slice(start, end);

    pageData.forEach((stock, rowIndex) => {
        const row = document.createElement('tr');
        const threeDayCanvasId = `three-day-chart-${rowIndex}`;
        const threeDayTooltipCanvasId = `three-day-tooltip-chart-${rowIndex}`;
        const hasRecentData = stock.recent_data && stock.recent_data.length > 0;

        let rowHTML = `
            <td>${stock.StockCode}</td>
            <td>${stock.StockName}</td>
            <td class="${parseFloat(stock.PopularityRank) < 300 ? 'highlight-red' : ''}">${stock.PopularityRank ?? 'N/A'}</td>
            <td>${stock.TurnoverAmount ?? 'N/A'}</td>
            <td class="${parseFloat(stock.TurnoverRank) < 300 ? 'highlight-red' : ''}">${stock.TurnoverRank ?? 'N/A'}</td>
            <td>${stock.LatestLimitUpDate ?? 'N/A'}</td>
            <td>${stock.ReasonCategory ?? 'N/A'}</td>
            <td class="candlestick-cell">
                ${hasRecentData ? `
                    <canvas id="${threeDayCanvasId}" width="100" height="60"></canvas>
                    <div class="tooltip">
                        <canvas id="${threeDayTooltipCanvasId}" width="300" height="180"></canvas>
                    </div>
                ` : 'N/A'}
            </td>
        `;

        if (hasRecentData) {
            for (let i = 0; i < 3; i++) {
                const data = stock.recent_data[i];
                const change = data && data.change_percent !== null && data.change_percent !== undefined ? data.change_percent : 0;
                let className = '';
                if (typeof change === 'number') {
                    className = change > 0 ? 'positive' : (change < 0 ? 'negative' : '');
                } else if (!hasRecentData && i === 0) {
                    rowHTML += `<td class="no-data" colspan="3">No recent data available</td>`;
                    break;
                }
                const canvasId = `chart-${rowIndex}-${i}`;
                const tooltip = data ? `
                    <div class="tooltip">
                        Open: ${data.open}<br>
                        Close: ${data.close}<br>
                        High: ${data.high}<br>
                        Low: ${data.low}<br>
                        <canvas id="${canvasId}" width="200" height="120"></canvas>
                    </div>
                ` : '';
                if (hasRecentData) {
                    rowHTML += `<td class="${className}">${change === 0 ? '0' : change}${tooltip}</td>`;
                }
            }
        } else {
            rowHTML += `<td class="no-data" colspan="3">No recent data available</td>`;
        }

        row.innerHTML = rowHTML;
        tbody.appendChild(row);

        if (hasRecentData) {
            const threeDayCanvas = document.getElementById(threeDayCanvasId);
            if (threeDayCanvas) {
                createCandlestickChart(threeDayCanvasId, stock.recent_data.slice(0, 3), true);
            }
            const threeDayTooltipCanvas = document.getElementById(threeDayTooltipCanvasId);
            if (threeDayTooltipCanvas) {
                createCandlestickChart(threeDayTooltipCanvasId, stock.recent_data.slice(0, 3), false);
            }
            for (let i = 0; i < 3 && i < stock.recent_data.length; i++) {
                const data = stock.recent_data[i];
                const canvasId = `chart-${rowIndex}-${i}`;
                const canvas = document.getElementById(canvasId);
                if (canvas) {
                    createCandlestickChart(canvasId, [data], false);
                }
            }
        }
    });

    renderPagination(pagination);

    const tableContainer = document.querySelector('.table-container');
    let rowCount = tableContainer.querySelector('.row-count');
    if (!rowCount) {
        rowCount = document.createElement('div');
        rowCount.className = 'row-count';
        rowCount.style.cssText = 'text-align: right; padding: 5px; font-size: 14px; color: #666;';
        tableContainer.insertBefore(rowCount, tableContainer.firstChild);
    }
    rowCount.textContent = `Rows: ${filteredData.length}`;

    // 重新绑定排序事件
    setTimeout(() => {
        makeTableSortable();
        bindSortEvents(filteredData, sortRules, renderTable, saveState);
    }, 0);
}

function saveState() {
    const state = {
        currentPage: pagination.currentPage,
        perPage: pagination.perPage,
        stockData,
        filteredData,
        sortRules,
        date: document.getElementById('date').value,
        thSectorIndex: document.getElementById('th_sector_index').value,
        sectorName: document.getElementById('sector_name').value,
        sectorCode: document.getElementById('sector_code').value
    };
    sessionStorage.setItem(`${PAGE_KEY}_state`, JSON.stringify(state));
}