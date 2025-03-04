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
let filteredData = [];  // 新增
let sortRules = [];
let deletedStocks = new Set();
let lastSyncedStockData = [];  // 记录上次同步的 stockData，用于比较


const BASE_URL = `http://${HOST}:${PORT}`;
const PAGE_KEY = 'custom_stock_dashboard';

document.addEventListener('DOMContentLoaded', function() {
    const savedState = JSON.parse(sessionStorage.getItem(`${PAGE_KEY}_state`));
    if (savedState) {
        pagination.currentPage = savedState.currentPage || 1;
        pagination.perPage = savedState.perPage || 30;
        stockData = savedState.stockData || [];
        filteredData = stockData;  // 初始化 filteredData
        sortRules = savedState.sortRules || [];
        deletedStocks = new Set(savedState.deletedStocks || []);
        lastSyncedStockData = [...stockData];  // 初始化上次同步数据
        document.getElementById('perPage').value = pagination.perPage;
        document.getElementById('newStockCode').value = savedState.newStockCode || '';
        if (stockData.length > 0) {
            updatePagination(pagination, stockData.length);
            renderTable();
        } else {
            fetchData();
        }
    } else {
        fetchData();
    }

    makeTableSortable();
    bindPerPageInput(pagination, filteredData, renderTable, saveState);
    bindSortEvents(filteredData, sortRules, renderTable, saveState);

    // 注册实时更新处理, 用于更新股票数据,第一个参数实际上不是命名空间，而是特定的event名称
    registerUpdateHandler('realtime_update', 'StockCode', (data) => {
        updateData(data, stockData, 'StockCode');
        applyFilters(); // 重新过滤数据
        updatePagination(pagination, filteredData.length); // 更新分页信息
        sortData(filteredData, sortRules, sortRules.length > 0 ? sortRules[0].field : 'StockCode', { shiftKey: false });
        renderTable();
        saveState();
    });


    // 监听 sessionStorage 变化，确保 watchlist 更新
    window.addEventListener('storage', function(event) {
        if (event.key === `${PAGE_KEY}_state`) {
            const newState = JSON.parse(event.newValue || '{}');
            stockData = newState.stockData || [];
            deletedStocks = new Set(newState.deletedStocks || []);
            updatePagination(pagination, stockData.length);
            renderTable();
            saveState();
        }
    });

    document.getElementById('search')?.addEventListener('input', applyFilters);  // 添加搜索事件

    const fetchDataBtn = document.getElementById('fetchDataBtn');
    if (fetchDataBtn) {
        console.log('fetchDataBtn found, binding click event');
        fetchDataBtn.addEventListener('click', function() {
            console.log('Fetch Data button clicked');
            // 可选：清除本地缓存并重置状态
            sessionStorage.removeItem(`${PAGE_KEY}_state`);
            console.log('Local storage cleared');
            pagination = initPagination();
            stockData = [];
            sortRules = [];
            deletedStocks.clear();
            document.getElementById('perPage').value = pagination.perPage;
            document.getElementById('newStockCode').value = '';
            fetchData();
        });
    } else {
        console.error('fetchDataBtn not found in DOM');
    }

    // 添加股票事件
    document.getElementById('newStockCode')?.addEventListener('keypress', function(event) {
        if (event.key === 'Enter') {
            addStock();
        }
    });

    // 保存股票代码事件
    const saveStockCodesBtn = document.getElementById('saveStockCodesBtn');
    if (saveStockCodesBtn) {
        saveStockCodesBtn.addEventListener('click', saveStockCodes);
    } else {
        console.error('saveStockCodesBtn not found in DOM');
    }

    // 新增：手动同步按钮
    const syncBtn = document.getElementById('syncToBackendBtn');
    if (syncBtn) {
        syncBtn.addEventListener('click', syncToBackend);
    } else {
        console.error('syncToBackendBtn not found in DOM');
    }

    // 每隔10分钟自动保存到后端
    setInterval(autoSaveStockCodes, 10 * 60 * 1000);  // 10分钟 = 600秒 = 600,000毫秒

    // 每分钟检测 watchlist 变化并同步
    setInterval(checkAndSyncWatchlist, 30 * 1000);  // 每30s检查一次
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
            console.log('Fetched data:', data);
            if (!Array.isArray(data)) {
                throw new Error('Expected an array but received: ' + JSON.stringify(data));
            }

            if (newStockCode) {
                stockData = stockData.concat(data.filter(stock => stock.StockCode === newStockCode));
            } else {
                stockData = data;
            }
            stockData = stockData.filter(stock => !deletedStocks.has(stock.StockCode));
            updatePagination(pagination, stockData.length);
            renderTable();
            saveState();
            document.getElementById('debugText').textContent = JSON.stringify(data, null, 2);
        })
        .catch(error => {
            console.error('Error fetching data:', error);
            document.getElementById('debugText').textContent = `Error: ${error.message}`;
            stockData = [];
            renderTable();
            saveState();
        });
}


function applyFilters() {  // 新增
    const searchValue = document.getElementById('search').value.toLowerCase();
    filteredData = stockData.filter(stock =>
        !deletedStocks.has(stock.StockCode) &&
        (stock.StockCode.toLowerCase().includes(searchValue) || stock.StockName.toLowerCase().includes(searchValue))
    );
    // **每次筛选后，自动按照当前的排序规则重新排序**
    if (sortRules.length > 0) {
        sortData(filteredData, sortRules, sortRules[0].field, { shiftKey: false });
    }
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
    updatePagination(pagination, stockData.length);
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
        lastSyncedStockData = [...stockData];  // 更新上次同步数据
        saveState();
    })
    .catch(error => {
        console.error('Error saving stock codes:', error);
        alert('Failed to save stock codes');
        saveState();
    });
}

// 新增：自动保存函数
function autoSaveStockCodes() {
    const stockCodes = stockData.map(stock => stock.StockCode);
    fetch(`${BASE_URL}/api/save_stock_codes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stock_codes: stockCodes })
    })
    .then(response => {
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        return response.json();
    })
    .then(data => {
        console.log('Auto-saved stock codes:', data.message || 'Success');
        lastSyncedStockData = [...stockData];  // 更新上次同步数据
        saveState();
    })
    .catch(error => {
        console.error('Auto-save failed:', error);
    });
}

// 新增：检查 watchlist 变化并同步
function checkAndSyncWatchlist() {
    const currentState = JSON.parse(sessionStorage.getItem(`${PAGE_KEY}_state`) || '{}');
    const currentStockData = currentState.stockData || [];

    // 比较当前 stockData 与上次同步的 stockData
    const hasChanged = JSON.stringify(currentStockData.map(s => s.StockCode)) !== JSON.stringify(lastSyncedStockData.map(s => s.StockCode));
    if (hasChanged) {
        console.log('Watchlist changed, syncing to backend...');
        stockData = [...currentStockData];  // 更新本地 stockData
        syncToBackend();
    }
}

// 新增：手动同步到后端
function syncToBackend() {
    const stockCodes = stockData.map(stock => stock.StockCode);
    fetch(`${BASE_URL}/api/update_stocks_pool`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ codes: stockCodes, caller: 'custom_stock_dashboard' })
    })
    .then(response => {
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        return response.json();
    })
    .then(data => {
        console.log('Synced to backend:', data.message || 'Success');
        lastSyncedStockData = [...stockData];  // 更新上次同步数据
        // 同时保存到文件（与 saveStockCodes 一致）
        saveStockCodes();
        return fetch(`${BASE_URL}/api/save_stock_codes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_codes: stockCodes })
        });
    })
    .then(response => response.json())
    .then(data => console.log('Saved to backend:', data.message || 'Success'))
    .catch(error => console.error('Sync failed:', error));
}

function updateTableHeaders() {
    const thead = document.getElementById('tableHeader').querySelector('tr');
    while (thead.children.length > 13) {
        thead.removeChild(thead.lastChild);
    }
}

function renderTable() {
    const tbody = document.querySelector('#stockTable tbody');
    if (!tbody) {
        console.error('tbody not found in DOM');
        return;
    }
    tbody.innerHTML = '';

    const start = (pagination.currentPage - 1) * pagination.perPage;
    const end = Math.min(start + pagination.perPage, filteredData.length);  // 使用 filteredData
    const pageData = filteredData.slice(start, end);  // 使用 filteredData

    console.log('Rendering table with pageData:', pageData);

    pageData.forEach((stock, rowIndex) => {
        const row = document.createElement('tr');
        const fiveDayCanvasId = `five-day-chart-${rowIndex}`;
        const fiveDayTooltipCanvasId = `five-day-tooltip-chart-${rowIndex}`;
        const hasRecentData = stock.recent_data && stock.recent_data.length > 0;

        const yesterdayChange = stock.YesterdayChange !== null && stock.YesterdayChange !== undefined ? stock.YesterdayChange : 'N/A';
        const yesterdayChangeClass = typeof yesterdayChange === 'number' ? (yesterdayChange > 0 ? 'positive' : yesterdayChange < 0 ? 'negative' : '') : '';
        const realtimeChange = stock.RealtimeChange !== null && stock.RealtimeChange !== undefined ? stock.RealtimeChange : 'N/A';
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
            <td class="${parseFloat(stock.PopularityRank) < 300 ? 'highlight-red' : ''}">${stock.PopularityRank || 'N/A'}</td>
            <td>${stock.TurnoverAmount || 'N/A'}</td>
            <td class="${parseFloat(stock.TurnoverRank) < 300 ? 'highlight-red' : ''}">${stock.TurnoverRank || 'N/A'}</td>
            <td>${stock.LatestLimitUpDate || 'N/A'}</td>
            <td>${stock.ReasonCategory || 'N/A'}</td>
            <td class="${yesterdayChangeClass}">${yesterdayChange === 0 ? '0' : yesterdayChange}</td>
            <td class="${realtimeChangeClass}">${realtimeChange === 0 ? '0' : realtimeChange}</td>
            <td>${stock.RealtimePrice || 'N/A'}</td>
            <td>${stock.YesterdayClose || 'N/A'}</td>
            <td><button class="btn delete-btn" data-stock-code="${stock.StockCode}">Delete</button></td>
        `;

        row.innerHTML = rowHTML;
        tbody.appendChild(row);

        const deleteBtn = row.querySelector('.delete-btn');
        deleteBtn.addEventListener('click', () => deleteStock(stock.StockCode));

        // console.log(`Rendered row ${rowIndex}:`, stock);

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
    rowCount.textContent = `Rows: ${filteredData.length}`;  // 使用 stockData

    console.log('Table rendered with pageData length:', pageData.length);
}

function saveState() {
    const state = {
        currentPage: pagination.currentPage,
        perPage: pagination.perPage,
        stockData,
        sortRules,
        deletedStocks: [...deletedStocks],
        newStockCode: document.getElementById('newStockCode').value
    };
    sessionStorage.setItem(`${PAGE_KEY}_state`, JSON.stringify(state));
    console.log('State saved to sessionStorage');
}