
# Monkey patch 以支持 gevent 对 zmq 的非阻塞
from gevent import monkey
import gevent.lock
import gevent.queue
monkey.patch_all()

from flask_socketio import emit
from app_init import app, socketio
import tushare as ts
import gevent
import time
import requests
import yaml
from datetime import datetime, time as dt_time
import zmq
from gevent.pool import Pool


logger = app.logger
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)
DATA_SOURCES = config['data_sources']

# 验证配置，如 update_interval
required_keys = ['tushare', 'mairui', 'selenium']
for source in required_keys:
    if source not in DATA_SOURCES or 'update_interval' not in DATA_SOURCES[source]:
        logger.error(f"Missing configuration for {source}")
        raise ValueError(f"Missing configuration for {source}")

ts.set_token(DATA_SOURCES['tushare']['token'])
pro = ts.pro_api()

stock_update_queue = gevent.queue.Queue()

def is_trading_time():
    now = datetime.now()
    weekday = now.weekday()
    if weekday > 4:  # 周末
        return False
    current_time = now.time()
    morning_start = dt_time(9, 30)
    morning_end = dt_time(11, 30)
    afternoon_start = dt_time(13, 0)
    afternoon_end = dt_time(15, 0)
    return (morning_start <= current_time <= morning_end) or (afternoon_start <= current_time <= afternoon_end)

def get_stock_prefix(stock_code):
    first_char = stock_code[0]
    first_digit = int(first_char)
    if first_digit in {0, 3}:
        return 'sz'
    elif first_digit == 6:
        return 'sh'
    return ''

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

    @staticmethod
    def selenium_adapter(data_dict):
        updated_data = {}
        code = data_dict.get("Stock Code", "").replace('sz', '').replace('sh', '')
        price_str = data_dict.get("最新", "0")
        prev_close_str = data_dict.get("昨收", "0")
        try:
            price = float(price_str.replace(',', '')) if price_str else 0
            prev_close = float(prev_close_str.replace(',', '')) if prev_close_str else 0
            updated_data[code] = {
                'RealtimePrice': price,
                'RealtimeChange': round(((price - prev_close) / prev_close * 100) if prev_close != 0 else 0, 2)
            }
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse Playwright data for {code}: {e}")
        return updated_data

class RealtimeUpdater:
    def __init__(self):
        self.stocks_pool = {}  # 使用普通字典
        self.realtime_data = {}  # 使用普通字典
        self.realtime_lock = gevent.lock.Semaphore()
        self.running = False
        self.source_tasks = {}
        self.custom_stocks = []  # 添加默认空列表
        self.zmq_context = zmq.Context()  # 单例上下文
        self.zmq_socket = self.zmq_context.socket(zmq.REQ)
        self.zmq_socket.connect("tcp://127.0.0.1:5555")

    def get_stock_suffix(self, stock_code):
        first_char = stock_code[0]
        first_digit = int(first_char)
        if first_digit in {0, 3}:
            return '.SZ'
        elif first_digit == 6:
            return '.SH'
        return ''
    

    def fetch_selenium_async(self, stock_codes, caller):
        start_time = time.time()
        socket = self.zmq_socket
        batch_size = 10 #min(30, max(10, len(stock_codes) // 5))  # 动态批次大小 ,如果股票代码太多，就设置批次大小为 30
        pool = Pool(3)  # 并发 3 个批次

        def fetch_batch(batch_codes, batch_num, total_batches):
            request = {"stocks": batch_codes}
            retries = 1
            for attempt in range(retries + 1):
                try:
                    socket.send_json(request)
                    timeout = 15000 #min(15000, max(8000, len(batch_codes) * 1000))  # 每只股票 1 秒，最小 8 秒，最大 15 秒
                    if socket.poll(timeout):
                        data = socket.recv_json()
                        logger.debug(f"[{caller}] Gevent Selenium batch {batch_num}/{total_batches} received: {data}")
                        with self.realtime_lock:
                            self.realtime_data.update(data)
                        try:
                            socketio.emit('realtime_update', data, namespace='/stocks_realtime')
                        except Exception as e:
                            logger.error(f"[{caller}] Error emitting realtime_update: {e}")
                        
                        break
                    else:
                        logger.warning(f"[{caller}] Timeout waiting for Selenium batch {batch_num}, attempt {attempt + 1}")
                        if attempt == retries:
                            break
                except Exception as e:
                    logger.error(f"[{caller}] Error in Selenium batch {batch_num}: {e}")
                    break

        batches = [stock_codes[i:i + batch_size] for i in range(0, len(stock_codes), batch_size)]
        total_batches = len(batches)
        pool.map(lambda idx: fetch_batch(batches[idx], idx + 1, total_batches), range(total_batches))
        pool.join()
        logger.info(f"[{caller}] Fetching {len(stock_codes)} stocks via Selenium took {time.time() - start_time:.2f} seconds")


    def get_realtime_data(self, stock_codes, source, caller='global'):
        with app.app_context():
            try:
                updated_data = {}
                stock_codes = list(set(stock_codes))
                logger.debug(f"[{caller}] Fetching {source} for {len(stock_codes)} stocks: {stock_codes}")

                if source == 'selenium':
                    gevent.spawn(self.fetch_selenium_async, stock_codes, caller)  # 异步执行
                    with self.realtime_lock:
                        updated_data = {code: self.realtime_data.get(code, {}) for code in stock_codes}

                    filted_data = {k: v for k, v in updated_data.items() if v} # 过滤掉值为空字典的股票代码               
                    logger.debug(f"[global] 'selenium' get realtime data: {filted_data}")
                    return filted_data

                elif source == 'tushare':
                    ts_codes = [f"{code}{self.get_stock_suffix(code)}" for code in stock_codes]
                    batch_size = DATA_SOURCES[source].get('batch_size', 10)
                    for i in range(0, len(ts_codes), batch_size):
                        batch = ts_codes[i:i + batch_size]
                        df = ts.realtime_quote(ts_code=','.join(batch))
                        batch_data = DataAdapter.tushare_adapter(df)
                        updated_data.update(batch_data)
                        gevent.sleep(60 / DATA_SOURCES[source]['limits']['per_minute'])

                elif source == 'mairui':
                    codes_to_fetch = stock_codes
                    batch_size = DATA_SOURCES[source].get('batch_size', 10)
                    for code in codes_to_fetch[:batch_size]:
                        urls = [DATA_SOURCES[source]['main_url'], DATA_SOURCES[source]['backup_url']]
                        success = False
                        for url_template in urls:
                            url = url_template.format(code=code, licence=DATA_SOURCES[source]['licence'])
                            try:
                                response = requests.get(url, timeout=5)
                                response.raise_for_status()
                                data = response.json()
                                batch_data = DataAdapter.mairui_adapter(data, code)
                                updated_data.update(batch_data)
                                success = True
                                break
                            except requests.RequestException as e:
                                logger.warning(f"[{caller}] Mairui request failed for {code}: {str(e)}")
                        if not success:
                            logger.error(f"[{caller}] Failed to fetch {code} from mairui")
                        gevent.sleep(DATA_SOURCES[source]['rate_limit'])
                
                if updated_data:
                    with self.realtime_lock:
                        self.realtime_data.update(updated_data)
                logger.debug(f"[{caller}] {source} returned {len(updated_data)} stocks: {updated_data}")
                return updated_data
            except Exception as e:
                logger.error(f"[{caller}] Error fetching {source} data: {str(e)}", exc_info=True)
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
                        with self.realtime_lock:
                            for code in codes:
                                if code in self.stocks_pool:
                                    self.stocks_pool[code]['sources'].add(caller)
                                    self.stocks_pool[code]['last_updated'] = current_time
                                else:
                                    self.stocks_pool[code] = {'sources': {caller}, 'last_updated': current_time}
                            logger.debug(f"[global] Updated stocks_pool: {self.stocks_pool}")
                        updated = True
                    except gevent.queue.Empty:
                        break

                # stocks_pool里面的stocks，如果超过两小时，还没有接到前端来的更新要求，将从池子里删去。      
                with self.realtime_lock:
                    expired = [code for code, info in self.stocks_pool.items() 
                              if time.time() - info['last_updated'] > 7200]
                    for code in expired:
                        del self.stocks_pool[code]
                        if code in self.realtime_data:
                            del self.realtime_data[code]
                    if expired:
                        logger.debug(f"[global] Removed expired stocks: {expired}")
                gevent.sleep(5)
            except Exception as e:
                logger.error(f"[global] Error in pool update task: {str(e)}", exc_info=True)
                gevent.sleep(5)

    def data_update_task(self, source):
        logger.info(f"[global] Starting {source} data update task")
        while self.running:
            try:
                with self.realtime_lock:
                    self.custom_stocks = [key for key, value in self.stocks_pool.items() if 'custom_stock' in value['sources']]
                    if source == 'mairui':
                        local_stock_codes = [code for code in self.stocks_pool.keys() if code in self.custom_stocks]
                    elif source == 'tushare':
                        local_stock_codes = [code for code in self.stocks_pool.keys() if code not in self.custom_stocks]
                    elif source == 'selenium': # for selenium
                        local_stock_codes = [code for code in self.stocks_pool.keys() if code not in self.custom_stocks]
                    else:
                        logger.error(f"[global] Error source {source} ")
                        return

                if not self.stocks_pool:
                    logger.debug(f"[global] Stocks pool is empty for {source}, skipping update")

                if local_stock_codes:
                    with app.app_context():
                        # 只更新超过 60 秒未更新的股票
                        expired_codes = [code for code in local_stock_codes if code not in self.realtime_data or 
                                        (time.time() - self.realtime_data[code].get('last_updated', 0) > 60)]
                        if expired_codes:
                            logger.debug(f"[global] {source} updating {len(expired_codes)} expired stocks")
                            updated_data = self.get_realtime_data(expired_codes, source, caller=f'{source}_task')
                            if updated_data:
                                with self.realtime_lock:
                                    for code, data in updated_data.items():
                                        data['last_updated'] = time.time()
                                        self.realtime_data[code] = data
                                try:
                                    socketio.emit('realtime_update', updated_data, namespace='/stocks_realtime')
                                    logger.debug(f"[global] {source} emitted: {updated_data}")
                                except Exception as e:
                                    logger.error(f"[global] Error emitting realtime_update: {e}")

                        else:
                            logger.debug(f"[global] No expired stocks to update for {source}")

                in_trading_time = is_trading_time()
                sleep_time = DATA_SOURCES[source]['update_interval']['non_trading_time'] if not in_trading_time else DATA_SOURCES[source]['update_interval']['trading_time']

                '''
                from blueprints.common import is_tradingday
                is_trading_day_flag = is_tradingday(datetime.now().date())
                current_time = datetime.now().time()
                morning_start = dt_time(9, 10)

                if is_trading_day_flag and current_time < morning_start:
                    now = datetime.now()
                    target_time = now.replace(hour=9, minute=10, second=0, microsecond=0) #如果当前时间早于 9:10，gevent.sleep(time_diff) 执行
                    if now < target_time:
                        time_diff = (target_time - now).total_seconds()
                        gevent.sleep(time_diff)
                else:
                    gevent.sleep(sleep_time)
                '''
                gevent.sleep(sleep_time)
            except Exception as e:
                logger.error(f"[global] Error in {source} data update task: {str(e)}", exc_info=True)
                gevent.sleep(60)
        logger.info(f"[global] {source} data update task stopped")

    def start(self):
        if not self.running:
            with app.app_context():
                self.sync_latest_stocks()
                logger.debug(f"[global] Initial stocks_pool: {self.stocks_pool}")
                
            self.running = True
            socketio.start_background_task(self.pool_update_task)
            #self.source_tasks['tushare'] = socketio.start_background_task(self.data_update_task, 'tushare')
            self.source_tasks['mairui'] = socketio.start_background_task(self.data_update_task, 'mairui')
            self.source_tasks['selenium'] = socketio.start_background_task(self.data_update_task, 'selenium')
            logger.info("[global] Realtime updater started with multi-source tasks")
            gevent.sleep(1)

    def stop(self):
        self.running = False
        self.zmq_socket.close()  # 关闭 socket
        self.zmq_context.term()  # 终止上下文
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
            with self.realtime_lock:
                for code in custom_stocks:
                    if code in self.stocks_pool:
                        self.stocks_pool[code]['sources'].add('custom_stock')
                        self.stocks_pool[code]['last_updated'] = current_time
                    else:
                        self.stocks_pool[code] = {'sources': {'custom_stock'}, 'last_updated': current_time}
                for code in limitup_stocks:
                    if code in self.stocks_pool:
                        self.stocks_pool[code]['sources'].add('limitup_unfilled_orders')
                        self.stocks_pool[code]['last_updated'] = current_time
                    else:
                        self.stocks_pool[code] = {'sources': {'limitup_unfilled_orders'}, 'last_updated': current_time}
                for code in ma_strategy_stocks:
                    if code in self.stocks_pool:
                        self.stocks_pool[code]['sources'].add('ma_strategy')
                        self.stocks_pool[code]['last_updated'] = current_time
                    else:
                        self.stocks_pool[code] = {'sources': {'ma_strategy'}, 'last_updated': current_time}
                logger.debug(f"[global] Synced initial stocks_pool: {self.stocks_pool}")

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
