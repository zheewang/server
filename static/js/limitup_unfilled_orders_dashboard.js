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
let pinnedStocks = new Set(JSON.parse(sessionStorage.getItem('pinnedStocks') || '[]'));
let hiddenStocks = new Set(JSON.parse(sessionStorage.getItem('hiddenStocks') || '[]'));

const BASE_URL = `http://${HOST}:${PORT}`;
const PAGE_KEY = 'limitup_unfilled_orders_dashboard';

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
        filteredData = savedState.filteredData || [...stockData]; // 确保初始化
        sortRules = savedState.sortRules || [];
        document.getElementById('perPage').value = pagination.perPage;
        document.getElementById('date').value = savedState.date || '';
        document.getElementById('search').value = savedState.search || '';
        document.getElementById('streakFilter').value = savedState.streakFilter || 'All';
        if (stockData.length > 0) {
            console.log('Loaded from sessionStorage:', stockData.length, 'items');
            populateStreakFilter();
            updatePagination(pagination, filteredData.length);
            applyFilters();
            renderTable();
        }
    } else {
        fetchData();
    }

    // 延迟绑定排序事件，确保 DOM 和数据就绪
    setTimeout(() => {
        makeTableSortable();
        bindSortEvents(filteredData, sortRules, renderTable, saveState);
        console.log('Sorting events bound');
    }, 0);

    bindPerPageInput(pagination, filteredData, renderTable, saveState);

    registerUpdateHandler('realtime_update', 'StockCode', (data) => {
        updateData(data, stockData, 'StockCode');
        applyFilters();
        renderTable(); // 明确调用 renderTable
        throttledSaveState();
    });

    document.getElementById('prevPage')?.addEventListener('click', () => changePage(pagination, -1, renderTable));
    document.getElementById('nextPage')?.addEventListener('click', () => changePage(pagination, 1, renderTable));
    document.getElementById('search')?.addEventListener('input', applyFilters);
    document.getElementById('streakFilter')?.addEventListener('change', applyFilters);

    const fetchDataBtn = document.getElementById('fetchDataBtn');
    if (fetchDataBtn) {
        fetchDataBtn.addEventListener('click', fetchData);
    }

    const showAllHiddenBtn = document.getElementById('showAllHiddenBtn');
    if (showAllHiddenBtn) {
        showAllHiddenBtn.addEventListener('click', showAllHidden);
    }
});

function fetchData() {
    pagination.currentPage = 1;
    pagination.perPage = parseInt(document.getElementById('perPage').value, 10) || 30;
    const date = document.getElementById('date').value;

    if (!date) {
        alert('Please select a date');
        console.log('No date selected, fetch aborted');
        return;
    }

    let url = `${BASE_URL}/api/limitup_unfilled_orders_data?date=${date}`;
    console.log('Fetching data with URL:', url);

    fetch(url)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.message || data.length === 0) {
                stockData = [];
                filteredData = [];
            } else {
                stockData = data;
                filteredData = [...stockData]; // 明确初始化
            }
            populateStreakFilter();
            applyFilters();
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
            applyFilters();
            saveState();
        });
}

function populateStreakFilter() {
    const streakFilter = document.getElementById('streakFilter');
    const currentValue = streakFilter.value;
    streakFilter.innerHTML = '<option value="All">All</option>';
    const streakDays = [...new Set(stockData.map(item => item.StreakDays).filter(day => day !== null))];
    streakDays.sort((a, b) => a - b);

    streakDays.forEach(day => {
        const option = document.createElement('option');
        option.value = `exact_${day}`;
        option.textContent = `${day}`;
        streakFilter.appendChild(option);
    });

    streakDays.forEach(day => {
        const gteOption = document.createElement('option');
        gteOption.value = `gte_${day}`;
        gteOption.textContent = `>= ${day}`;
        streakFilter.appendChild(gteOption);

        const lteOption = document.createElement('option');
        lteOption.value = `lte_${day}`;
        lteOption.textContent = `<= ${day}`;
        streakFilter.appendChild(lteOption);
    });

    streakFilter.value = currentValue && streakFilter.querySelector(`option[value="${currentValue}"]`) ? currentValue : 'All';
}

function applyFilters() {
    const searchValue = document.getElementById('search').value.toLowerCase();
    const streakFilter = document.getElementById('streakFilter').value;

    filteredData = stockData.filter(stock => {
        const searchMatch = !hiddenStocks.has(stock.StockCode) &&
            (stock.StockCode.toLowerCase().includes(searchValue) ||
             stock.StockName.toLowerCase().includes(searchValue));

        let streakMatch = true;
        if (streakFilter !== 'All' && stock.StreakDays !== null) {
            const [condition, value] = streakFilter.split('_');
            const streakValue = parseInt(value);
            if (condition === 'exact') {
                streakMatch = stock.StreakDays === streakValue;
            } else if (condition === 'gte') {
                streakMatch = stock.StreakDays >= streakValue;
            } else if (condition === 'lte') {
                streakMatch = stock.StreakDays <= streakValue;
            }
        }

        return searchMatch && streakMatch;
    });

    const pinned = filteredData.filter(stock => pinnedStocks.has(stock.StockCode));
    const unpinned = filteredData.filter(stock => !pinnedStocks.has(stock.StockCode));
    filteredData = [...pinned, ...unpinned];

    updatePagination(pagination, filteredData.length);
    renderTable();
}

function togglePin(stockCode) {
    if (pinnedStocks.has(stockCode)) {
        pinnedStocks.delete(stockCode);
    } else {
        pinnedStocks.add(stockCode);
    }
    sessionStorage.setItem('pinnedStocks', JSON.stringify([...pinnedStocks]));
    applyFilters();
}

function toggleHide(stockCode) {
    if (hiddenStocks.has(stockCode)) {
        hiddenStocks.delete(stockCode);
    } else {
        hiddenStocks.add(stockCode);
    }
    sessionStorage.setItem('hiddenStocks', JSON.stringify([...hiddenStocks]));
    applyFilters();
    updateShowAllHiddenButton();
}

function showAllHidden() {
    hiddenStocks.clear();
    sessionStorage.setItem('hiddenStocks', JSON.stringify([...hiddenStocks]));
    applyFilters();
    updateShowAllHiddenButton();
}

function updateShowAllHiddenButton() {
    const btn = document.getElementById('showAllHiddenBtn');
    if (btn) {
        btn.textContent = `Show All Hidden (${hiddenStocks.size})`;
        btn.disabled = hiddenStocks.size === 0;
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
    const end = Math.min(start + pagination.perPage, filteredData.length);
    const pageData = filteredData.slice(start, end);

    pageData.forEach((stock, rowIndex) => {
        const row = document.createElement('tr');
        const fiveDayCanvasId = `five-day-chart-${rowIndex}`;
        const fiveDayTooltipCanvasId = `five-day-tooltip-chart-${rowIndex}`;
        const hasRecentData = stock.recent_data && stock.recent_data.length > 0;

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
            <td>${stock.StreakDays ?? 'N/A'}</td>
            <td>${stock.OpeningAmount ?? 'N/A'}</td>
            <td>${stock.LimitUpOrderAmount ?? 'N/A'}</td>
            <td>${stock.FirstLimitUpTime ?? 'N/A'}</td>
            <td>${stock.FinalLimitUpTime ?? 'N/A'}</td>
            <td>${stock.LimitUpOpenTimes ?? 'N/A'}</td>
            <td class="${stock.PopularityRank && parseFloat(stock.PopularityRank) < 300 ? 'highlight-red' : ''}">${stock.PopularityRank ?? 'N/A'}</td>
            <td>${stock.TurnoverAmount ?? 'N/A'}</td>
            <td class="${stock.TurnoverRank && parseFloat(stock.TurnoverRank) < 300 ? 'highlight-red' : ''}">${stock.TurnoverRank ?? 'N/A'}</td>
            <td>${stock.ReasonCategory ?? 'N/A'}</td>
            <td class="${realtimeChangeClass}">${realtimeChange === 0 ? '0' : realtimeChange}</td>
            <td>${stock.RealtimePrice ?? 'N/A'}</td>
            <td>
                <button class="btn pin-btn" data-stock-code="${stock.StockCode}">${pinnedStocks.has(stock.StockCode) ? 'Unpin' : 'Pin'}</button>
                <button class="btn hide-btn" data-stock-code="${stock.StockCode}">${hiddenStocks.has(stock.StockCode) ? 'Show' : 'Hide'}</button>
            </td>
        `;

        row.innerHTML = rowHTML;
        tbody.appendChild(row);

        const pinBtn = row.querySelector('.pin-btn');
        const hideBtn = row.querySelector('.hide-btn');
        pinBtn.addEventListener('click', () => togglePin(stock.StockCode));
        hideBtn.addEventListener('click', () => toggleHide(stock.StockCode));

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

function saveState() {
    const state = {
        currentPage: pagination.currentPage,
        perPage: pagination.perPage,
        stockData,
        filteredData,
        sortRules,
        date: document.getElementById('date').value,
        search: document.getElementById('search').value,
        streakFilter: document.getElementById('streakFilter').value
    };
    sessionStorage.setItem(`${PAGE_KEY}_state`, JSON.stringify(state));
}