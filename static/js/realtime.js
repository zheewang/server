// static/js/realtime.js

// 使用全局 io，来自 socket.io.js
if (!window.io) {
    throw new Error('Socket.IO library not loaded');
}

// 此处必须加上监听特定的event命名空间，否则会导致连接失败
const socket = io.connect(`http://${window.HOST}:${window.PORT}/stocks_realtime`, { 
    transports: ['websocket', 'polling'], 
    reconnection: true, 
    reconnectionAttempts: 5 
});

// 实时更新处理函数映射
const updateHandlers = new Map();

// 初始化 WebSocket
function initRealtime() {
    socket.on('connect', () => console.log('WebSocket connected'));
    socket.on('disconnect', () => console.log('WebSocket disconnected'));
    socket.on('connect_error', (error) => console.error('WebSocket connect error:', error));
    socket.on('reconnect_attempt', (attempt) => console.log('Reconnection attempt:', attempt));
}

// 注册实时更新处理函数
function registerUpdateHandler(event, dataKey, handler) {
    const key = `${event}:${dataKey}`;
    if (updateHandlers.has(key)) {
        console.warn(`Handler for ${key} already registered, overwriting`);
    }
    updateHandlers.set(key, handler);

    socket.on(`${event}`, (data) => {
        console.log(`Received ${event}:`, data);
        if (handler) {
            handler(data);
        }
    });
}

// 更新数据通用方法
function updateData(data, stockData, key) {
    stockData.forEach(stock => {
        const realtime = data[stock[key]];
        if (realtime) {
            stock.RealtimeChange = realtime.RealtimeChange;
            stock.RealtimePrice = realtime.RealtimePrice;
        }
    });
}

initRealtime();

export { registerUpdateHandler, updateData };