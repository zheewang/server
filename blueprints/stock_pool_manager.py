from flask_socketio import emit
import gevent.lock
import gevent.queue
from app_init import  app, db
from app_init import socketio
import tushare as ts
import gevent
import time
import requests
import logging
import yaml
from datetime import datetime, time as dt_time
from blueprints.common import is_tradingday
from app_init import TradingDay  # 直接从 app_init.py 导入
from blueprints.custom_stock import read_stock_codes  # 引入 custom_stocks

logger = app.logger
# 假设你已经全局配置了 logging
def disable_logging_temporarily():
    logging.disable(logging.CRITICAL)  # 禁用所有级别低于 CRITICAL 的日志

def enable_logging_again():
    logging.disable(logging.NOTSET)  # 重新启用所有日志

# 在你的特定页面或模块中调用 disable_logging_temporarily()
# 在需要重新启用日志时调用 enable_logging_again()
enable_logging_again()

# 加载配置文件
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)
DATA_SOURCES = config['data_sources']
ts.set_token(DATA_SOURCES['tushare']['token'])
pro = ts.pro_api()

class DataAdapter:
    @staticmethod
    def tushare_adapter(df):
        updated_data = {}
        if not df.empty:
            for _, row in df.iterrows():
                code = row['TS_CODE'].split('.')[0]
                prev_close = float(row['PRE_CLOSE']) if row['PRE_CLOSE'] else 0
                price = float(row['PRICE']) if row['PRICE'] else 0
                updated_data[code] = {
                    'RealtimePrice': price,
                    'RealtimeChange': round(((price - prev_close) / prev_close * 100) if prev_close != 0 else 0, 2)
                }
        return updated_data
    
    @staticmethod
    def mairui_adapter(data, stock_code):
        updated_data = {}
        if data and isinstance(data, dict):
            price = float(data.get('p', 0))
            prev_close = float(data.get('yc', 0))
            updated_data[stock_code] = {
                'RealtimePrice': price,
                'RealtimeChange': float(data.get('pc', 0)) if data.get('pc') else round(((price - prev_close) / prev_close * 100) if prev_close != 0 else 0, 2)
            }
        return updated_data

stocks_pool = {}
stocks_pool_lock = gevent.lock.Semaphore()
stock_update_queue = gevent.queue.Queue()

def is_trading_time():
    now = datetime.now()
    weekday = now.weekday()
    if weekday > 4:
        return False
    current_time = now.time()
    morning_start = dt_time(9, 15)
    morning_end = dt_time(11, 30)
    afternoon_start = dt_time(13, 0)
    afternoon_end = dt_time(15, 0)
    return (morning_start <= current_time <= morning_end) or (afternoon_start <= current_time <= afternoon_end)

class RealtimeUpdater:
    def __init__(self):
        self.realtime_data = {}
        self.realtime_lock = gevent.lock.Semaphore()
        self.running = False
        self.last_cleanup_time = time.time()
        self.trading_day = TradingDay()  # 初始化 TradingDay
        self.custom_stocks = set(read_stock_codes())  # 加载 custom_stocks

    def get_stock_suffix(self, stock_code):
        first_char = stock_code[0]
        first_digit = int(first_char)
        if first_digit in {0, 3}:
            return '.SZ'
        elif first_digit == 6:
            return '.SH'
        return ''

    def select_data_source(self):
        if is_trading_time():
            source = 'tushare' # 'mairui'
        else:
            source = 'tushare'
        logger.debug(f"Selected data source: {source} based on trading time")
        return source

    def get_realtime_data(self, stock_codes, caller='global', source=None):
        try:
            # 判断是否为交易日和交易时间
            is_trading_day = is_tradingday(datetime.now().date())
            in_trading_time = is_trading_time()
            
            updated_data = {}
            if not is_trading_day or not in_trading_time:
                # 非交易日或非交易时间，全部从 tushare 获取
                logger.debug(f"[{caller}] Non-trading day/time, fetching all from tushare")
                ts_codes = [f"{code}{self.get_stock_suffix(code)}" for code in stock_codes]
                batch_size = DATA_SOURCES['tushare'].get('batch_size', 10)
                for i in range(0, len(ts_codes), batch_size):
                    batch = ts_codes[i:i + batch_size]
                    logger.debug(f"[{caller}] Fetching batch {i // batch_size + 1}: {batch}")
                    df = ts.realtime_quote(ts_code=','.join(batch))
                    batch_data = DataAdapter.tushare_adapter(df)
                    logger.debug(f"[{caller}] Tushare batch data: {batch_data}")
                    updated_data.update(batch_data)

            else:
                # 交易日交易时间，分情况处理
                custom_codes = [code for code in stock_codes if code in self.custom_stocks]
                other_codes = [code for code in stock_codes if code not in self.custom_stocks]

                # custom_stocks 从 mairui 获取
                if custom_codes:
                    logger.debug(f"[{caller}] Fetching custom stocks from mairui: {custom_codes}")
                    rate_limit = DATA_SOURCES['mairui']['rate_limit']
                    for code in stock_codes:
                        max_retries = 3
                        retry_count = 0
                        success = False
                        
                        while retry_count < max_retries and not success:
                            urls = [DATA_SOURCES['mairui']['main_url'], DATA_SOURCES['mairui']['backup_url']]
                            for url_template in urls:
                                url = url_template.format(code=code, licence=DATA_SOURCES['mairui']['licence'])
                                try:
                                    response = requests.get(url, timeout=5)
                                    response.raise_for_status()
                                    data = response.json()
                                    logger.debug(f"[{caller}] Fetched data for {code}: {data}")
                                    batch_data = DataAdapter.mairui_adapter(data, code)
                                    logger.debug(f"[{caller}] Mairui batch data for {code}: {batch_data}")
                                    updated_data.update(batch_data)
                                    success = True
                                    break
                                except requests.RequestException as e:
                                    logger.warning(f"[{caller}] Attempt {retry_count + 1} failed for {code} with {url}: {str(e)}")
                                    retry_count += 1
                                    if retry_count < max_retries:
                                        gevent.sleep(1)
                            if not success :
                                logger.error(f"[{caller}] Failed to fetch data for {code} after {max_retries} retries")
                        gevent.sleep(1 / rate_limit)
                    # 其他股票从 tushare 获取
                if other_codes:
                    logger.debug(f"[{caller}] Fetching other stocks from tushare: {other_codes}")
                    ts_codes = [f"{code}{self.get_stock_suffix(code)}" for code in other_codes]
                    batch_size = DATA_SOURCES['tushare'].get('batch_size', 10)
                    for i in range(0, len(ts_codes), batch_size):
                        batch = ts_codes[i:i + batch_size]
                        df = ts.realtime_quote(ts_code=','.join(batch))
                        batch_data = DataAdapter.tushare_adapter(df)
                        updated_data.update(batch_data)

            logger.debug(f"[{caller}] Final updated_data from {source}")
            return updated_data
        except Exception as e:
            logger.error(f"[{caller}] Error fetching realtime data from {source}: {str(e)}", exc_info=True)
            return {}

    def pool_update_task(self):
        logger.info("[global] Starting pool update task")
        while self.running:
            try:
                updated = False
                while True:
                    try:
                        new_stocks = stock_update_queue.get_nowait()
                        caller = new_stocks.get('caller', 'unknown')
                        codes = new_stocks.get('codes', [])
                        logger.debug(f"[global] Retrieved from queue: {codes} from {caller}")
                        current_time = time.time()
                        with stocks_pool_lock:
                            for code in codes:
                                if code in stocks_pool:
                                    stocks_pool[code]['sources'].add(caller)
                                    stocks_pool[code]['last_updated'] = current_time
                                else:
                                    stocks_pool[code] = {'sources': {caller}, 'last_updated': current_time}
                            logger.debug(f"[global] Updated stocks_pool: {stocks_pool}")
                        updated = True
                    except gevent.queue.Empty:
                        break
                with stocks_pool_lock:
                    expired = [code for code, info in stocks_pool.items() 
                              if time.time() - info['last_updated'] > 7200]
                    for code in expired:
                        del stocks_pool[code]
                    if expired:
                        logger.debug(f"[global] Removed expired stocks: {expired}")
                gevent.sleep(5)
            except Exception as e:
                logger.error(f"[global] Error in pool update task: {str(e)}", exc_info=True)
                gevent.sleep(5)  # 继续循环

    def data_update_task(self):
        logger.info("[global] Starting data update task")
        while self.running:
            try:
                logger.debug(f"[global] Task running: {self.running}")
                with stocks_pool_lock:
                    local_stock_codes = list(stocks_pool.keys())
                
                if local_stock_codes:
                    with app.app_context():
                        logger.debug("[global] Calling get_realtime_data")
                        updated_data = self.get_realtime_data(local_stock_codes, caller='data_task')
                        logger.debug(f"[global] Returned updated_data")
                        if updated_data:
                            with self.realtime_lock:
                                self.realtime_data.clear()
                                self.realtime_data.update(updated_data)
                            #发送实时更新处理, 第一个参数是特定的event名称
                            socketio.emit('realtime_update', updated_data, namespace='/stocks_realtime')
                            logger.debug(f"[global] realtime Emitted ")
                        else:
                            logger.warning("[global] No data returned from get_realtime_data")
                gevent.sleep(10)
            except Exception as e:
                logger.error(f"[global] Error in data update task: {str(e)}", exc_info=True)
                gevent.sleep(5)  # 继续循环
        logger.info("[global] Data update task stopped")

    def start(self):
        if not self.running:
            with app.app_context():
                self.sync_latest_stocks()  # 确保启动时填充
                logger.debug(f"[global] Initial stocks_pool after sync: {stocks_pool}")
            self.running = True
            socketio.start_background_task(self.pool_update_task)
            socketio.start_background_task(self.data_update_task)
            logger.info("[global] Realtime updater started with background tasks")
            gevent.sleep(1)  # 等待任务启动
            logger.debug("[global] Background tasks started")

    def stop(self):
        self.running = False
        logger.info("[global] Realtime updater stopped")

    def sync_latest_stocks(self):
        with app.app_context():
            from blueprints.custom_stock import read_stock_codes
            from blueprints.limitup_unfilled_orders import get_latest_limitup_stocks
            from blueprints.ma_strategy import get_latest_ma_strategy_stocks
            custom_stocks = read_stock_codes()
            limitup_stocks = get_latest_limitup_stocks()
            ma_strategy_stocks = get_latest_ma_strategy_stocks()
            current_time = time.time()
            with stocks_pool_lock:
                for code in custom_stocks:
                    if code in stocks_pool:
                        stocks_pool[code]['sources'].add('custom_stock')
                        stocks_pool[code]['last_updated'] = current_time
                    else:
                        stocks_pool[code] = {'sources': {'custom_stock'}, 'last_updated': current_time}
                for code in limitup_stocks:
                    if code in stocks_pool:
                        stocks_pool[code]['sources'].add('limitup_unfilled_orders')
                        stocks_pool[code]['last_updated'] = current_time
                    else:
                        stocks_pool[code] = {'sources': {'limitup_unfilled_orders'}, 'last_updated': current_time}
                for code in ma_strategy_stocks:
                    if code in stocks_pool:
                        stocks_pool[code]['sources'].add('ma_strategy')
                        stocks_pool[code]['last_updated'] = current_time
                    else:
                        stocks_pool[code] = {'sources': {'ma_strategy'}, 'last_updated': current_time}
                logger.debug(f"[global] Synced initial stocks_pool: {stocks_pool}")

global_updater = RealtimeUpdater()

def update_stocks_pool(new_stock_codes, caller='unknown'):
    logger.debug(f"[{caller}] Attempting to queue stock codes: {new_stock_codes}")
    if not new_stock_codes:
        logger.warning(f"[{caller}] No stock codes to queue")
    else:
        stock_update_queue.put({'caller': caller, 'codes': list(new_stock_codes)})

def get_realtime_data():
    with global_updater.realtime_lock:
        return global_updater.realtime_data.copy()