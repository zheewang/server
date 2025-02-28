// static/js/common.js

// 全局 chartInstances 对象，模拟 Chart.js 的 destroy 方法
if (!window.chartInstances) {
    window.chartInstances = {};
}

function createCandlestickChart(canvasId, dataArray, hideXAxis = false) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !dataArray || dataArray.length === 0) {
        console.error(`Invalid canvas ${canvasId} or data`);
        return null;
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) {
        console.error(`Failed to get 2D context for canvas ${canvasId}`);
        return null;
    }

    // 清空画布
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 数据准备
    const chartData = dataArray
        .slice()
        .sort((a, b) => new Date(a.trading_Date) - new Date(b.trading_Date))
        .map(data => ({
            x: data.trading_Date,
            o: parseFloat(data.open),
            h: parseFloat(data.high),
            l: parseFloat(data.low),
            c: parseFloat(data.close),
            change_percent: data.change_percent !== null && data.change_percent !== undefined ? parseFloat(data.change_percent) : 0
        }));

    const maxHigh = Math.max(...chartData.map(d => d.h));
    const minLow = Math.min(...chartData.map(d => d.l));
    const priceRange = maxHigh - minLow;
    const padding = priceRange * 0.1;

    const candleWidth = hideXAxis ? 5 : 15; // 缩小一半：缩略图 5px，完整图 15px
    const spacing = hideXAxis ? 5 : 10;
    const totalWidth = chartData.length * (candleWidth + spacing) - spacing;
    const chartHeight = canvas.height - (hideXAxis ? 10 : 20); // 留出底部空间给日期

    // 缩放比例
    const scaleX = (canvas.width - spacing) / totalWidth;
    const scaleY = chartHeight / (priceRange + padding * 2);

    // 绘制蜡烛图
    chartData.forEach((day, index) => {
        const x = index * (candleWidth + spacing) * scaleX;
        const open = chartHeight - (day.o - minLow + padding) * scaleY;
        const close = chartHeight - (day.c - minLow + padding) * scaleY;
        const high = chartHeight - (day.h - minLow + padding) * scaleY;
        const low = chartHeight - (day.l - minLow + padding) * scaleY;

        // 绘制影线
        ctx.beginPath();
        ctx.moveTo(x + candleWidth * scaleX / 2, high);
        ctx.lineTo(x + candleWidth * scaleX / 2, low);
        ctx.strokeStyle = '#000000';
        ctx.lineWidth = 1;
        ctx.stroke();

        // 绘制实体
        ctx.fillStyle = day.c > day.o ? '#e74c3c' : '#2ecc71'; // A股：红涨绿跌
        const candleHeight = Math.abs(open - close);
        ctx.fillRect(x, Math.min(open, close), candleWidth * scaleX, candleHeight || 1); // 确保至少 1px 高度

        // 绘制 change_percent
        const change = day.change_percent;
        ctx.fillStyle = change > 0 ? '#e74c3c' : '#2ecc71'; // A股：红涨绿跌
        ctx.font = hideXAxis ? '8px Arial' : '12px Arial';
        ctx.textAlign = 'center';
        const textY = high - 5; // 顶部标注
        ctx.fillText(`${change.toFixed(2)}%`, x + candleWidth * scaleX / 2, textY > 0 ? textY : 10);

        // 绘制交易日期（X 轴）
        if (!hideXAxis) {
            ctx.fillStyle = '#000000';
            ctx.font = '10px Arial';
            ctx.fillText(day.x.slice(-5), x + candleWidth * scaleX / 2, canvas.height - 5); // 显示日期后 5 位（如 "01-03"）
        }
    });

    // console.log(`Created canvas chart ${canvasId} with data:`, chartData);
    const chart = {
        destroy: () => ctx.clearRect(0, 0, canvas.width, canvas.height)
    };
    chartInstances[canvasId] = chart;
    return chart;
}