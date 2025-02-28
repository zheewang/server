// static/js/utils.js

function initPagination(perPageDefault = 30) {
    return {
        currentPage: 1,
        totalPages: 1,
        perPage: perPageDefault,
    };
}

function updatePagination(pagination, dataLength) {
    pagination.totalPages = Math.ceil(dataLength / pagination.perPage);
    if (pagination.currentPage > pagination.totalPages) pagination.currentPage = pagination.totalPages;
    if (pagination.currentPage < 1) pagination.currentPage = 1;
}

function renderPagination(pagination) {
    const pageInfo = document.getElementById('pageInfo');
    if (pageInfo) {
        pageInfo.innerText = `Page ${pagination.currentPage} of ${pagination.totalPages}`;
    }
    const prevPage = document.getElementById('prevPage');
    const nextPage = document.getElementById('nextPage');
    if (prevPage) prevPage.disabled = pagination.currentPage === 1;
    if (nextPage) nextPage.disabled = pagination.currentPage === pagination.totalPages;
}

function changePage(pagination, offset, renderFn) {
    pagination.currentPage += offset;
    if (pagination.currentPage < 1) pagination.currentPage = 1;
    if (pagination.currentPage > pagination.totalPages) pagination.currentPage = pagination.totalPages;
    renderFn();
}

function bindPerPageInput(pagination, data, renderFn, saveFn) {
    const perPageInput = document.getElementById('perPage');
    if (perPageInput) {
        perPageInput.addEventListener('input', function() {
            const newPerPage = parseInt(this.value, 10) || 30;
            if (newPerPage !== pagination.perPage) {
                pagination.perPage = newPerPage;
                pagination.currentPage = 1;
                updatePagination(pagination, data.length);
                renderFn();
                saveFn();
            }
        });
    }
}

function sortData(data, sortRules, field, event) {
    const isShift = event.shiftKey;
    const existingRuleIndex = sortRules.findIndex(rule => rule.field === field);

    if (existingRuleIndex >= 0) {
        if (sortRules[existingRuleIndex].direction === 'asc') {
            sortRules[existingRuleIndex].direction = 'desc';
        } else {
            sortRules.splice(existingRuleIndex, 1);
        }
    } else {
        const direction = 'asc';
        if (isShift && sortRules.length > 0) {
            sortRules.push({ field, direction });
        } else {
            sortRules.length = 0; // 清空旧规则，确保单列排序时只保留当前规则
            sortRules.push({ field, direction });
        }
    }

    console.log('Sort rules updated:', JSON.stringify(sortRules));

    data.sort((a, b) => {
        for (let rule of sortRules) {
            let valA, valB;
            if (rule.field.startsWith('recentChange')) {
                const index = parseInt(rule.field.replace('recentChange', ''));
                valA = a.recent_data && a.recent_data[index] ? a.recent_data[index].change_percent : -Infinity;
                valB = b.recent_data && b.recent_data[index] ? b.recent_data[index].change_percent : -Infinity;
            } else {
                valA = a[rule.field] || '';
                valB = b[rule.field] || '';
            }

            if (['PopularityRank', 'TurnoverAmount', 'TurnoverRank', 'YesterdayChange', 'YesterdayClose', 'RealtimeChange', 'RealtimePrice', 'StreakDays', 'OpeningAmount', 'LimitUpOrderAmount', 'LimitUpOpenTimes'].includes(rule.field) || rule.field.startsWith('recentChange')) {
                valA = valA === '' || valA === null || valA === 'N/A' ? -Infinity : parseFloat(valA) || 0;
                valB = valB === '' || valB === null || valB === 'N/A' ? -Infinity : parseFloat(valB) || 0;
            } else {
                valA = valA.toString().toLowerCase();
                valB = valB.toString().toLowerCase();
            }

            const comparison = valA > valB ? 1 : (valA < valB ? -1 : 0);
            if (comparison !== 0) {
                return rule.direction === 'asc' ? comparison : -comparison;
            }
        }
        return 0;
    });
}

function updateSortIndicators(sortRules) {
    const headers = document.querySelectorAll('#stockTable th[data-sort]');
    console.log('Updating sort indicators for headers:', headers.length, 'Sort rules:', JSON.stringify(sortRules));
    headers.forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        const field = th.dataset.sort;
        const originalText = th.dataset.text || th.textContent.trim();
        const ruleIndex = sortRules.findIndex(rule => rule.field === field);
        if (ruleIndex >= 0) {
            const direction = sortRules[ruleIndex].direction;
            th.classList.add(`sort-${direction}`);
            th.textContent = `${originalText} ${ruleIndex + 1}${direction === 'asc' ? '↑' : '↓'}`;
            console.log(`Applied to th: field=${field}, direction=${direction}, index=${ruleIndex + 1}, class=${th.className}, text=${th.textContent}`);
        } else {
            th.textContent = originalText;
            console.log(`Reset th: field=${field}, text=${th.textContent}`);
        }
    });
}

function bindSortEvents(data, sortRules, renderFn, saveFn) {
    const headers = document.querySelectorAll('#stockTable th[data-sort]');
    console.log('Binding sort events to headers:', headers.length);
    headers.forEach(th => {
        th.removeEventListener('click', th._sortHandler);
        th._sortHandler = (event) => {
            const field = th.dataset.sort;
            console.log(`Sorting triggered for field: ${field}`);
            sortData(data, sortRules, field, event);
            updateSortIndicators(sortRules); // 立即更新表头
            renderFn();
            saveFn();
        };
        th.addEventListener('click', th._sortHandler);
    });
}

function makeTableSortable() {
    const thead = document.getElementById('tableHeader')?.querySelector('tr');
    if (!thead) return;
    let draggedTh = null;

    thead.addEventListener('dragstart', (e) => {
        if (e.target.tagName === 'TH') {
            draggedTh = e.target;
            e.dataTransfer.setData('text/plain', draggedTh.cellIndex);
        }
    });

    thead.addEventListener('dragover', (e) => e.preventDefault());

    thead.addEventListener('drop', (e) => {
        e.preventDefault();
        if (e.target.tagName === 'TH' && draggedTh !== e.target) {
            const fromIndex = draggedTh.cellIndex;
            const toIndex = e.target.cellIndex;
            const ths = Array.from(thead.children);
            if (fromIndex < toIndex) {
                thead.insertBefore(draggedTh, ths[toIndex].nextSibling);
            } else {
                thead.insertBefore(draggedTh, ths[toIndex]);
            }
            reorderTableColumns(fromIndex, toIndex);
        }
        draggedTh = null;
    });

    Array.from(thead.children).forEach(th => th.draggable = true);
}

function reorderTableColumns(fromIndex, toIndex) {
    const tbody = document.querySelector('#stockTable tbody');
    if (!tbody) return;
    Array.from(tbody.children).forEach(row => {
        const cells = Array.from(row.children);
        if (fromIndex < toIndex) {
            row.insertBefore(cells[fromIndex], cells[toIndex].nextSibling);
        } else {
            row.insertBefore(cells[fromIndex], cells[toIndex]);
        }
    });
}

export {
    initPagination,
    updatePagination,
    renderPagination,
    changePage,
    bindPerPageInput,
    sortData,
    updateSortIndicators,
    bindSortEvents,
    makeTableSortable,
    reorderTableColumns
};