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

import { registerUpdateHandler, updateData } from './realtime.js';

let pagination = initPagination();
let stockData = [];
let filteredData = [];
let sortRules = [];
let recentDates = [];

const BASE_URL = `http://${HOST}:${PORT}`;
const PAGE_KEY = 'ma_strategy_dashboard';

document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, registering handler');
    // 注册实时更新处理, 用于更新股票数据,第一个参数实际上不是命名空间，而是特定的event名称
    registerUpdateHandler('realtime_update', 'StockCode', (data) => {
        //console.log('Updating stockData with:', data);
        updateData(data, stockData, 'StockCode');
        applyFilters();
        renderTable();
        saveState();
    });

    makeTableSortable();

    const savedState = JSON.parse(sessionStorage.getItem(`${PAGE_KEY}_state`));
    if (savedState) {
        pagination.currentPage = savedState.currentPage || 1;
        pagination.perPage = savedState.perPage || 30;
        stockData = savedState.stockData || [];
        filteredData = savedState.filteredData || [];
        sortRules = savedState.sortRules || [];
        document.getElementById('perPage').value = pagination.perPage;
        document.getElementById('date').value = savedState.date || '';
        document.getElementById('typeFilter').value = savedState.typeFilter || 'All';
        if (stockData.length > 0) {
            populateTypeFilter();
            updatePagination(pagination, filteredData.length);
            updateTableHeaders();
            renderTable();
        }
    }

    bindPerPageInput(pagination, filteredData, renderTable, saveState);

  

    bindSortEvents(filteredData, sortRules, renderTable, saveState);

    // 绑定 Fetch Data 按钮事件
    document.getElementById('fetchDataBtn')?.addEventListener('click', fetchData);

    document.getElementById('prevPage')?.addEventListener('click', () => changePage(pagination, -1, renderTable));
    document.getElementById('nextPage')?.addEventListener('click', () => changePage(pagination, 1, renderTable));

    // 绑定 typeFilter 的 change 事件
    const typeFilter = document.getElementById('typeFilter');
    if (typeFilter) {
        typeFilter.addEventListener('change', applyFilters);
    } else {
        console.error('typeFilter element not found');
    }
});

function fetchData() {
    const date = document.getElementById('date').value;
    pagination.perPage = parseInt(document.getElementById('perPage').value, 10) || 30;

    if (!date) {
        alert('Please enter a date');
        return;
    }

    const url = `${BASE_URL}/api/ma_strategy_data?date=${date}`;
    console.log('Fetching data with URL:', url);

    fetch(url)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            console.log('Fetched data:', data);
            if (!Array.isArray(data)) {
                throw new Error('Expected an array but received: ' + JSON.stringify(data));
            }

            stockData = data;
            populateTypeFilter();
            applyFilters();
        })
        .catch(error => {
            console.error('Error fetching data:', error);
            stockData = [];
            filteredData = [];
            renderTable();
            saveState();
        });
}

function populateTypeFilter() {
    const typeFilter = document.getElementById('typeFilter');
    const currentValue = typeFilter.value;
    typeFilter.innerHTML = '<option value="All">All</option>';
    const types = [...new Set(stockData.map(item => item.type).filter(type => type !== null && type !== undefined))];
    types.sort();

    types.forEach(type => {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = type;
        typeFilter.appendChild(option);
    });

    typeFilter.value = currentValue && typeFilter.querySelector(`option[value="${currentValue}"]`) ? currentValue : 'All';
}

function applyFilters() {
    const typeFilter = document.getElementById('typeFilter').value;
    console.log('Applying filter with type:', typeFilter);
    filteredData = stockData.filter(stock => 
        typeFilter === 'All' || stock.type === typeFilter
    );
    console.log('Filtered data length:', filteredData.length);

    updateTableHeaders();
    updatePagination(pagination, filteredData.length);
    renderTable();
    saveState();
}

function updateTableHeaders() {
    if (filteredData.length > 0 && filteredData[0].recent_data && filteredData[0].recent_data.length > 0) {
        recentDates = filteredData[0].recent_data.map(item => item.trading_Date).slice(0, 5);
        const thead = document.getElementById('tableHeader').querySelector('tr');
        while (thead.children.length > 12) {
            thead.removeChild(thead.lastChild);
        }
        recentDates.forEach((date, index) => {
            const th = document.createElement('th');
            th.textContent = date;
            th.dataset.sort = `recentChange${index}`;
            th.dataset.text = date;
            thead.appendChild(th);
        });
        bindSortEvents(filteredData, sortRules, renderTable, saveState);
    } else {
        const thead = document.getElementById('tableHeader').querySelector('tr');
        while (thead.children.length > 12) {
            thead.removeChild(thead.lastChild);
        }
    }
}

function renderTable() {
    const tbody = document.querySelector('#stockTable tbody');
    tbody.innerHTML = '';

    const start = (pagination.currentPage - 1) * pagination.perPage;
    const end = start + pagination.perPage;
    const pageData = filteredData.slice(start, end);

    pageData.forEach((stock, rowIndex) => {
        const row = document.createElement('tr');
        const fiveDayCanvasId = `five-day-chart-${rowIndex}`;
        const fiveDayTooltipCanvasId = `five-day-tooltip-chart-${rowIndex}`;
        const hasRecentData = stock.recent_data && stock.recent_data.length > 0;
        
        const realtimeChange = stock.RealtimeChange !== null && stock.RealtimeChange !== undefined ? stock.RealtimeChange : 'N/A';
        const realtimeChangeClass = typeof realtimeChange === 'number' ? (realtimeChange > 0 ? 'positive' : realtimeChange < 0 ? 'negative' : '') : '';

        let rowHTML = `
            <td>${stock.StockCode}</td>
            <td>${stock.StockName}</td>
            <td>${stock.trading_Date}</td>
            <td>${stock.type}</td>
            <td class="${parseFloat(stock.PopularityRank) < 300 ? 'highlight-red' : ''}">${stock.PopularityRank || 'N/A'}</td>
            <td>${stock.TurnoverAmount || 'N/A'}</td>
            <td class="${parseFloat(stock.TurnoverRank) < 300 ? 'highlight-red' : ''}">${stock.TurnoverRank || 'N/A'}</td>
            <td>${stock.LatestLimitUpDate || 'N/A'}</td>
            <td>${stock.ReasonCategory || 'N/A'}</td>
            <td class="candlestick-cell">
                ${hasRecentData ? `
                    <canvas id="${fiveDayCanvasId}" width="100" height="60"></canvas>
                    <div class="tooltip">
                        <canvas id="${fiveDayTooltipCanvasId}" width="300" height="180"></canvas>
                    </div>
                ` : 'N/A'}
            </td>
            <td class="${realtimeChangeClass}">${realtimeChange === 0 ? '0' : realtimeChange}</td>
            <td>${stock.RealtimePrice || 'N/A'}</td>
        `;

        if (hasRecentData) {
            for (let i = 0; i < 5; i++) {
                const data = stock.recent_data[i];
                const change = data && data.change_percent !== null && data.change_percent !== undefined ? data.change_percent : 0;
                let className = '';
                if (typeof change === 'number') {
                    className = change > 0 ? 'positive' : (change < 0 ? 'negative' : '');
                } else if (!hasRecentData && i === 0) {
                    rowHTML += `<td class="no-data" colspan="5">No recent data available</td>`;
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
            rowHTML += `<td class="no-data" colspan="5">No recent data available</td>`;
        }

        row.innerHTML = rowHTML;
        tbody.appendChild(row);

        if (hasRecentData) {
            const fiveDayCanvas = document.getElementById(fiveDayCanvasId);
            if (fiveDayCanvas) {
                createCandlestickChart(fiveDayCanvasId, stock.recent_data.slice(0, 5), true);
            } else {
                console.error(`Five-day canvas ${fiveDayCanvasId} not found`);
            }

            const fiveDayTooltipCanvas = document.getElementById(fiveDayTooltipCanvasId);
            if (fiveDayTooltipCanvas) {
                createCandlestickChart(fiveDayTooltipCanvasId, stock.recent_data.slice(0, 5), false);
            } else {
                console.error(`Five-day tooltip canvas ${fiveDayTooltipCanvasId} not found`);
            }

            for (let i = 0; i < 5 && i < stock.recent_data.length; i++) {
                const data = stock.recent_data[i];
                const canvasId = `chart-${rowIndex}-${i}`;
                const canvas = document.getElementById(canvasId);
                if (canvas) {
                    createCandlestickChart(canvasId, [data], false);
                } else {
                    console.error(`Canvas ${canvasId} not found`);
                }
            }
        }
    });

    renderPagination(pagination);

    // 添加行数统计
    const tableContainer = document.querySelector('.table-container');
    let rowCount = tableContainer.querySelector('.row-count');
    if (!rowCount) {
        rowCount = document.createElement('div');
        rowCount.className = 'row-count';
        rowCount.style.cssText = 'text-align: right; padding: 5px; font-size: 14px; color: #666;';
        tableContainer.insertBefore(rowCount, tableContainer.firstChild);
    }
    rowCount.textContent = `Rows: ${filteredData.length}`;  // 显示总行数

    bindSortEvents(filteredData, sortRules, renderTable, saveState);
}

function saveState() {
    const state = {
        currentPage: pagination.currentPage,
        perPage: pagination.perPage,
        stockData,
        filteredData,
        sortRules,
        date: document.getElementById('date').value,
        typeFilter: document.getElementById('typeFilter').value
    };
    sessionStorage.setItem(`${PAGE_KEY}_state`, JSON.stringify(state));
}