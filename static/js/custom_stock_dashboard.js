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
let deletedStocks = new Set();

const BASE_URL = `http://${HOST}:${PORT}`;
const PAGE_KEY = 'custom_stock_dashboard';

function throttle(fn, delay) {
    let lastCall = 0;
    return function (...args) {
        const now = Date.now();
        if (now - lastCall >= delay) {
            fn(...args);
            lastCall = now;
        }
    };
}

const throttledSaveState = throttle(saveState, 5000);

document.addEventListener('DOMContentLoaded', function() {
    const savedState = JSON.parse(sessionStorage.getItem(`${PAGE_KEY}_state`));
    if (savedState) {
        pagination.currentPage = savedState.currentPage || 1;
        pagination.perPage = savedState.perPage || 30;
        stockData = savedState.stockData || [];
        filteredData = savedState.filteredData || [...stockData]; 
        sortRules = savedState.sortRules || [];
        deletedStocks = new Set(savedState.deletedStocks || []);
        document.getElementById('perPage').value = pagination.perPage;
        document.getElementById('newStockCode').value = savedState.newStockCode || '';

        if (stockData.length > 0) {
            // 渲染
            updatePagination(pagination, filteredData.length);
            renderTable();
            updateSortIndicators(sortRules);
            bindSortEvents(filteredData, sortRules, renderTable, saveState);
        }
          
    } else {
        fetchData();
    }

    // 延迟绑定排序事件
    setTimeout(() => {
        makeTableSortable();
        bindSortEvents(filteredData, sortRules, renderTable, saveState);
        console.log('Sorting events bound');
    }, 0);

    bindPerPageInput(pagination, filteredData, renderTable, saveState);

    registerUpdateHandler('realtime_update', 'StockCode', (data) => {
        const updated = updateData(data, stockData, 'StockCode');
        if (updated) {
            applyFilters(); // 同步 filteredData
            updatePagination(pagination, filteredData.length);
            renderTable();
            throttledSaveState();
            console.log('Table updated with realtime data');
        }
    });

    document.getElementById('search')?.addEventListener('input', applyFilters);

    const fetchDataBtn = document.getElementById('fetchDataBtn');
    if (fetchDataBtn) {
        fetchDataBtn.addEventListener('click', function() {
            sessionStorage.removeItem(`${PAGE_KEY}_state`);
            pagination = initPagination();
            stockData = [];
            filteredData = [];
            sortRules = [];
            deletedStocks.clear();
            document.getElementById('perPage').value = pagination.perPage;
            document.getElementById('newStockCode').value = '';
            fetchData();
        });
    }

    document.getElementById('newStockCode')?.addEventListener('keypress', function(event) {
        if (event.key === 'Enter') {
            addStock();
        }
    });

    const saveStockCodesBtn = document.getElementById('saveStockCodesBtn');
    if (saveStockCodesBtn) {
        saveStockCodesBtn.addEventListener('click', saveStockCodes);
    }
});

function fetchData(newStockCode = null) {
    pagination.perPage = parseInt(document.getElementById('perPage').value, 10) || 30;

    let url = `${BASE_URL}/api/custom_stock_data`;
    if (newStockCode) {
        url += `?new_stock_code=${newStockCode}`;
    }
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
            if (data.length === 0) {
                console.warn('No data returned from API');
            }
            console.log('Fetched data:', data);

            if (newStockCode) {
                stockData = stockData.concat(data.filter(stock => stock.StockCode === newStockCode));
            } else {
                stockData = data;
            }
            applyFilters(); // 同步 filteredData
            updatePagination(pagination, filteredData.length);
            renderTable();
            saveState();
            //document.getElementById('debugText').textContent = JSON.stringify(data, null, 2);
            // 重新绑定排序事件
            setTimeout(() => {
                makeTableSortable();
                bindSortEvents(filteredData, sortRules, renderTable, saveState);
            }, 0);
        })
        .catch(error => {
            console.error('Error fetching data:', error);
            document.getElementById('debugText').textContent = `Error: ${error.message}`;
            stockData = [];
            filteredData = [];
            renderTable();
            saveState();
        });
}

function applyFilters() {
    const searchValue = document.getElementById('search').value.toLowerCase();
    filteredData = stockData.filter(stock =>
        !deletedStocks.has(stock.StockCode) &&
        (stock.StockCode.toLowerCase().includes(searchValue) || stock.StockName.toLowerCase().includes(searchValue))
    );
    console.log('Filtered data length:', filteredData.length);
    updatePagination(pagination, filteredData.length);
    renderTable();
    saveState();
}

function addStock() {
    const newStockCode = document.getElementById('newStockCode').value.trim();
    if (newStockCode && !stockData.some(stock => stock.StockCode === newStockCode)) {
        fetchData(newStockCode);
        document.getElementById('newStockCode').value = '';
        saveState();
    }
}

function deleteStock(stockCode) {
    deletedStocks.add(stockCode);
    stockData = stockData.filter(stock => stock.StockCode !== stockCode);
    applyFilters();
    updatePagination(pagination, filteredData.length);
    renderTable();
    saveState();
}

function saveStockCodes() {
    const stockCodes = stockData.map(stock => stock.StockCode);
    fetch(`${BASE_URL}/api/save_stock_codes`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ stock_codes: stockCodes })
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            alert(data.message || 'Stock codes saved successfully');
            saveState();
        })
        .catch(error => {
            console.error('Error saving stock codes:', error);
            alert('Failed to save stock codes');
            saveState();
        });
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

    console.log('Rendering table with pageData length:', pageData.length);

    pageData.forEach((stock, rowIndex) => {
        const row = document.createElement('tr');
        const fiveDayCanvasId = `five-day-chart-${rowIndex}`;
        const fiveDayTooltipCanvasId = `five-day-tooltip-chart-${rowIndex}`;
        const hasRecentData = stock.recent_data && stock.recent_data.length > 0;

        const yesterdayChange = stock.YesterdayChange ?? 'N/A';
        const yesterdayChangeClass = typeof yesterdayChange === 'number' ? (yesterdayChange > 0 ? 'positive' : yesterdayChange < 0 ? 'negative' : '') : '';
        const realtimeChange = stock.RealtimeChange ?? 'N/A';
        const realtimeChangeClass = typeof realtimeChange === 'number' ? (realtimeChange > 0 ? 'positive' : realtimeChange < 0 ? 'negative' : '') : '';

        let rowHTML = `
            <td>${stock.StockCode}</td>
            <td>${stock.StockName}</td>
            <td class="candlestick-cell">
                ${hasRecentData ? `
                    <canvas id="${fiveDayCanvasId}" width="100" height="60"></canvas>
                    <div class="tooltip">
                        <canvas id="${fiveDayTooltipCanvasId}" width="300" height="180"></canvas>
                    </div>
                ` : 'N/A'}
            </td>
            <td class="${parseFloat(stock.PopularityRank) < 300 ? 'highlight-red' : ''}">${stock.PopularityRank ?? 'N/A'}</td>
            <td>${stock.TurnoverAmount ?? 'N/A'}</td>
            <td class="${parseFloat(stock.TurnoverRank) < 300 ? 'highlight-red' : ''}">${stock.TurnoverRank ?? 'N/A'}</td>
            <td>${stock.LatestLimitUpDate ?? 'N/A'}</td>
            <td>${stock.ReasonCategory ?? 'N/A'}</td>
            <td class="${yesterdayChangeClass}">${yesterdayChange === 0 ? '0' : yesterdayChange}</td>
            <td class="${realtimeChangeClass}">${realtimeChange === 0 ? '0' : realtimeChange}</td>
            <td>${stock.RealtimePrice ?? 'N/A'}</td>
            <td>${stock.YesterdayClose ?? 'N/A'}</td>
            <td><button class="btn delete-btn" data-stock-code="${stock.StockCode}">Delete</button></td>
        `;

        row.innerHTML = rowHTML;
        tbody.appendChild(row);

        const deleteBtn = row.querySelector('.delete-btn');
        deleteBtn.addEventListener('click', () => deleteStock(stock.StockCode));

        if (hasRecentData) {
            const fiveDayCanvas = document.getElementById(fiveDayCanvasId);
            if (fiveDayCanvas) {
                createCandlestickChart(fiveDayCanvasId, stock.recent_data.slice(0, 5), true);
            }
            const fiveDayTooltipCanvas = document.getElementById(fiveDayTooltipCanvasId);
            if (fiveDayTooltipCanvas) {
                createCandlestickChart(fiveDayTooltipCanvasId, stock.recent_data.slice(0, 5), false);
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

function sortDataByRules(data, rules) {
    if (!Array.isArray(rules) || rules.length === 0) return data;
    return [...data].sort((a, b) => {
        for (const rule of rules) {
            const { field, direction } = rule;
            if (!field || !['asc', 'desc'].includes(direction)) continue;
            const valA = a[field];
            const valB = b[field];
            if (valA === valB) continue;
            if (valA == null) return direction === 'asc' ? 1 : -1;
            if (valB == null) return direction === 'asc' ? -1 : 1;
            if (valA < valB) return direction === 'asc' ? -1 : 1;
            if (valA > valB) return direction === 'asc' ? 1 : -1;
        }
        return 0;
    });
}

function saveState() {
    const state = {
        currentPage: pagination.currentPage,
        perPage: pagination.perPage,
        stockData,
        filteredData, // 保存 filteredData
        sortRules,
        deletedStocks: [...deletedStocks],
        newStockCode: document.getElementById('newStockCode').value
    };
    sessionStorage.setItem(`${PAGE_KEY}_state`, JSON.stringify(state));
}