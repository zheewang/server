// static/js/scripts.js

// 自定义蜡烛图插件
const candlestickPlugin = {
    id: 'candlestickPlugin',
    afterDatasetsDraw(chart) {
        const ctx = chart.ctx;
        ctx.save();

        // 绘制影线
        chart.data.datasets.forEach((dataset, datasetIndex) => {
            const meta = chart.getDatasetMeta(datasetIndex);
            meta.data.forEach((bar, index) => {
                const data = dataset.data[index];
                const high = chart.scales.y.getPixelForValue(data.h);
                const low = chart.scales.y.getPixelForValue(data.l);
                const open = chart.scales.y.getPixelForValue(data.o);
                const close = chart.scales.y.getPixelForValue(data.c);
                const x = bar.x;

                // 绘制影线
                ctx.beginPath();
                ctx.strokeStyle = data.c > data.o ? '#e74c3c' : '#2ecc71';
                ctx.lineWidth = 1;
                ctx.moveTo(x, high);
                ctx.lineTo(x, low);
                ctx.stroke();

                // 绘制 change_percent
                const changePercent = data.change_percent !== null ? data.change_percent.toFixed(2) : '0';
                ctx.font = '12px "Segoe UI"';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'bottom';
                ctx.fillStyle = data.change_percent >= 0 ? '#e74c3c' : '#2ecc71'; // 正数/0 红色，负数绿色
                ctx.fillText(`${changePercent}%`, x, high - 10); // 调整偏移为 -10
                console.log(`Drawing ${changePercent}% at x: ${x}, y: ${high - 10} for canvas ${chart.canvas.id}`);
            });
        });

        ctx.restore();
    }
};

Chart.register(candlestickPlugin);