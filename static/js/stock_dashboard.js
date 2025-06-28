
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
let sectors = [];


const BASE_URL = `http://${HOST}:${PORT}`;
const PAGE_KEY = 'stock_dashboard';

// 节流函数
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

// 创建节流版本的 saveState，每 5 秒最多保存一次
const throttledSaveState = throttle(saveState, 5000);


// 显示加载动画
function showLoadingSpinner(isFetchData = false) {
    const spinner = document.getElementById('loadingSpinner');
    if (spinner) {
        spinner.style.display = 'block';
    }
    // 根据上下文禁用按钮
    const fetchButton = document.getElementById('fetchDataBtn');
    const refreshButton = document.getElementById('refreshRealtimeBtn');
    if (isFetchData) {
        if (fetchButton) fetchButton.disabled = true;
        if (refreshButton) refreshButton.disabled = true;
    } else {
        if (fetchButton) fetchButton.disabled = true; // 仅禁用 fetchDataBtn
    }
}

// 隐藏加载动画
function hideLoadingSpinner(isFetchData = false) {
    const spinner = document.getElementById('loadingSpinner');
    if (spinner) {
        spinner.style.display = 'none';
    }
    // 根据上下文启用按钮
    const fetchButton = document.getElementById('fetchDataBtn');
    const refreshButton = document.getElementById('refreshRealtimeBtn');
    if (isFetchData) {
        if (fetchButton) fetchButton.disabled = false;
        if (refreshButton) refreshButton.disabled = false;
    } else {
        if (fetchButton) fetchButton.disabled = false; // 仅启用 fetchDataBtn
    }
}

document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, registering handler');
    // 注册实时更新处理
    registerUpdateHandler('realtime_update', 'StockCode', (data) => {
        updateData(data, stockData, 'StockCode');
        applyFilters();
        renderTable();
        throttledSaveState();
        hideLoadingSpinner(true); // WebSocket 更新后隐藏动画
    });

    makeTableSortable();

    // 加载保存的状态
    const savedState = JSON.parse(sessionStorage.getItem(`${PAGE_KEY}_state`));
    if (savedState) {
        pagination.currentPage = savedState.currentPage || 1;
        pagination.perPage = savedState.perPage || 30;
        stockData = savedState.stockData || [];
        filteredData = savedState.filteredData || [...stockData];
        sortRules = savedState.sortRules || [];
        document.getElementById('perPage').value = pagination.perPage;
        document.getElementById('date').value = savedState.date || '';
        document.getElementById('th_sector_index').value = savedState.thSectorIndex || '';
        document.getElementById('sector_code').value = savedState.sectorCodes ? savedState.sectorCodes.join(',') : '';
        document.getElementById('typeFilter').value = savedState.typeFilter || 'All';
        if (stockData.length > 0) {
            populateTypeFilter();
            updatePagination(pagination, filteredData.length);
            updateTableHeaders();
            renderTable();
        }
    } else {
        stockData = [];
        filteredData = [];
        renderTable();
    }

    showLoadingSpinner(); // 显示加载动画

    // 获取板块数据
    fetch(`${BASE_URL}/api/sectors`)
        .then(response => {
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
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

            // 根据 th_sector_index 或保存的状态填充 sector_name 选项
            const selectedTHS = savedState?.thSectorIndex || thsSelect.value;
            const sectorNameSelect = document.getElementById('sector_name');
            sectorNameSelect.innerHTML = ''; // 清空现有选项
            const currentSelections = savedState?.sectorCodes || [];
            const selectedSectors = currentSelections
                .map(code => sectors.find(sector => sector.SectorIndexCode === code))
                .filter(sector => sector);
            const filteredSectors = selectedTHS
                ? sectors.filter(sector => sector.THSSectorIndex === selectedTHS)
                : sectors;
            const allSectors = [
                ...selectedSectors,
                ...filteredSectors.filter(
                    sector => !selectedSectors.some(s => s.SectorIndexCode === sector.SectorIndexCode)
                )
            ];
            allSectors.forEach(sector => {
                const option = document.createElement('option');
                option.value = sector.SectorIndexCode;
                option.textContent = sector.SectorIndexName;
                option.dataset.code = sector.SectorIndexCode;
                sectorNameSelect.appendChild(option);
            });

            // 初始化 Select2
            $('#sector_name').select2({
                placeholder: 'Search a sector',
                allowClear: true,
                multiple: true
            });

            // 恢复 sector_name 的保存状态
            if (savedState?.sectorCodes) {
                const validCodes = savedState.sectorCodes.filter(code =>
                    Array.from(sectorNameSelect.options).some(opt => opt.value === code)
                );
                if (validCodes.length !== savedState.sectorCodes.length) {
                    console.warn('Some saved sector codes are invalid:', savedState.sectorCodes);
                }
                $('#sector_name').val(validCodes).trigger('change');
                document.getElementById('sector_code').value = validCodes.join(',');
            }

            // 绑定事件
            bindPerPageInput(pagination, filteredData, renderTable, saveState);
            bindSortEvents(filteredData, sortRules, renderTable, saveState);

            document.getElementById('fetchDataBtn')?.addEventListener('click', fetchData);
            document.getElementById('refreshRealtimeBtn')?.addEventListener('click', () => {
                console.log('Emitting refresh_realtime_data');
                window.socket.emit('refresh_realtime_data', { dashboards: ['stock_dashboard'] });
            });

            document.getElementById('prevPage')?.addEventListener('click', () => changePage(pagination, -1, renderTable));
            document.getElementById('nextPage')?.addEventListener('click', () => changePage(pagination, 1, renderTable));
            document.getElementById('search')?.addEventListener('input', applyFilters);

            // 处理 th_sector_index 变化
            document.getElementById('th_sector_index').addEventListener('change', function() {
                const selectedTHS = this.value;
                const sectorNameSelect = document.getElementById('sector_name');
                const sectorCodeInput = document.getElementById('sector_code');

                // 保存当前选择
                const currentSelections = $('#sector_name').val() || [];

                // 获取当前已选择的板块代码对应的完整板块信息
                const selectedSectors = currentSelections
                    .map(code => sectors.find(sector => sector.SectorIndexCode === code))
                    .filter(sector => sector);

                // 获取当前 THSSectorIndex 过滤的板块
                const filteredSectors = selectedTHS
                    ? sectors.filter(sector => sector.THSSectorIndex === selectedTHS)
                    : sectors;

                // 合并已选择的板块和过滤的板块，并去重
                const allSectors = [
                    ...selectedSectors,
                    ...filteredSectors.filter(
                        sector => !selectedSectors.some(s => s.SectorIndexCode === sector.SectorIndexCode)
                    )
                ];

                // 更新 sector_name 选项
                sectorNameSelect.innerHTML = '';
                allSectors.forEach(sector => {
                    const option = document.createElement('option');
                    option.value = sector.SectorIndexCode;
                    option.textContent = sector.SectorIndexName;
                    option.dataset.code = sector.SectorIndexCode;
                    sectorNameSelect.appendChild(option);
                });

                // 触发 Select2 更新
                $('#sector_name').trigger('select2:updated');
                $('#sector_name').val(currentSelections).trigger('change');
                sectorCodeInput.value = currentSelections.join(',');
                saveState();
            });

            // 处理 sector_name 变化
            $('#sector_name').on('change', function() {
                const selectedCodes = $(this).val() || [];
                document.getElementById('sector_code').value = selectedCodes.join(',');
                saveState();
            });

            const typeFilter = document.getElementById('typeFilter');
            if (typeFilter) {
                typeFilter.addEventListener('change', applyFilters);
            } else {
                console.error('typeFilter element not found');
            }
        })
        .catch(error => {
                    console.error('Error fetching sectors:', error);
                    alert('无法加载板块数据，请稍后重试');
                })
        .finally(() => {
            hideLoadingSpinner(); // 无论成功或失败，隐藏动画
        });
});


function fetchData() {
    const date = document.getElementById('date').value;
    const sectorCodes = document.getElementById('sector_code').value;
    pagination.perPage = parseInt(document.getElementById('perPage').value, 10) || 30;

    if (!date || !sectorCodes) {
        alert('Please enter a date and select at least one sector');
        return;
    }

    showLoadingSpinner(); // 显示加载动画

    const url = `${BASE_URL}/api/stock_data?date=${date}&sector_codes=${encodeURIComponent(sectorCodes)}`;
    console.log('Fetching data with URL:', url);

    fetch(url)
        .then(response => {
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            return response.json();
        })
        .then(data => {
            if (!Array.isArray(data)) throw new Error('Expected an array but received: ' + JSON.stringify(data));
            stockData = data.map(item => ({
                ...item,
                RealtimeChange: item.RealtimeChange ?? 'N/A',
                RealtimePrice: item.RealtimePrice ?? 'N/A'
            }));
            filteredData = [...stockData];
            populateTypeFilter();
            applyFilters(); // 直接调用 applyFilters
        })
        .catch(error => {
                    console.error('Error fetching data:', error);
                    stockData = [];
                    filteredData = [];
                    renderTable();
                    saveState();
                })
        .finally(() => {
            hideLoadingSpinner(); // 无论成功或失败，隐藏动画
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
    const searchValue = document.getElementById('search').value.toLowerCase();
    filteredData = stockData.filter(stock =>
        (typeFilter === 'All' || stock.type === typeFilter) &&
        (stock.StockCode.toLowerCase().includes(searchValue) || stock.StockName.toLowerCase().includes(searchValue))
    );
    updateTableHeaders();
    updatePagination(pagination, filteredData.length);
    renderTable();
    bindSortEvents(filteredData, sortRules, renderTable, saveState); // 移到此处
    saveState();
}

function updateTableHeaders() {
    if (filteredData.length > 0 && filteredData[0].recent_data && filteredData[0].recent_data.length > 0) {
        recentDates = filteredData[0].recent_data.map(item => item.trading_Date).slice(0, 3);
        const thead = document.getElementById('tableHeader').querySelector('tr');
        while (thead.children.length > 10) {
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
        while (thead.children.length > 10) {
            thead.removeChild(thead.lastChild);
        }
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
        const threeDayCanvasId = `three-day-chart-${rowIndex}`;
        const threeDayTooltipCanvasId = `three-day-tooltip-chart-${rowIndex}`;
        const hasRecentData = stock.recent_data && stock.recent_data.length > 0;

        const realtimeChange = stock.RealtimeChange ?? 'N/A';
        const realtimeChangeClass = typeof realtimeChange === 'number' ? (realtimeChange > 0 ? 'positive' : realtimeChange < 0 ? 'negative' : '') : '';

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
            <td class="${realtimeChangeClass}">${realtimeChange === 'N/A' ? 'N/A' : (realtimeChange === 0 ? '0.00%' : (realtimeChange > 0 ? '+' : '') + realtimeChange.toFixed(2) + '%')}</td>
            <td>${realtimeChange === 'N/A' ? 'N/A' : parseFloat(stock.RealtimePrice).toFixed(2)}</td>
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

    setTimeout(() => {
        makeTableSortable();
        bindSortEvents(filteredData, sortRules, renderTable, saveState);
    }, 0);
}

function saveState() {
    // 修改 12：保存多选板块代码
    const state = {
        currentPage: pagination.currentPage,
        perPage: pagination.perPage,
        stockData,
        filteredData,
        sortRules,
        date: document.getElementById('date').value,
        thSectorIndex: document.getElementById('th_sector_index').value,
        sectorCodes: $('#sector_name').val() || [],
    };
    sessionStorage.setItem(`${PAGE_KEY}_state`, JSON.stringify(state));
}