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

# 验证配置完整性
required_keys = {
    'tushare': ['token', 'update_interval', 'limits', 'batch_size'],
    'mairui': ['main_url', 'backup_url', 'licence', 'update_interval', 'rate_limit', 'batch_size'],
    'selenium': ['url_template', 'update_interval']
}
for source, keys in required_keys.items():
    if source not in DATA_SOURCES:
        logger.error(f"Missing configuration for {source}")
        raise ValueError(f"Missing configuration for {source}")
    for key in keys:
        if key not in DATA_SOURCES[source]:
            logger.error(f"Missing {key} in {source} configuration")
            raise ValueError(f"Missing {key} in {source} configuration")

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

def get_target_time(current_time):
    morning_start = dt_time(9, 10)
    morning_end = dt_time(11, 30)
    afternoon_start = dt_time(13, 0)
    
    if current_time < morning_start:
        return dt_time(9, 10)
    elif morning_end < current_time < afternoon_start:
        return dt_time(13, 0)
    return None

def sleep_until_target(target_time):
    now = datetime.now()
    time_diff = (now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0) - now).total_seconds()
    if time_diff > 0:
        gevent.sleep(time_diff)


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
            price = round(float(data.get('p', 0)), 2)  # MODIFIED - 保留小数点后两位
            prev_close = round(float(data.get('yc', 0)), 2)  # MODIFIED - 保留小数点后两位
            updated_data[stock_code] = {
                'RealtimePrice': price,
                'RealtimeChange': round(float(data.get('pc', 0)), 2) if data.get('pc') else round(((price - prev_close) / prev_close * 100) if prev_close != 0 else 0, 2)
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
        self.stocks_pool = {}
        self.realtime_data = {}
        self.realtime_lock = gevent.lock.Semaphore()
        self.running = False
        self.source_tasks = {}
        self.custom_stocks = []
        self.zmq_context = zmq.Context()
        self.pub_socket = self.zmq_context.socket(zmq.PUB)
        self.pub_socket.connect("tcp://127.0.0.1:5556")
        self.sub_socket = self.zmq_context.socket(zmq.SUB)
        self.sub_socket.connect("tcp://127.0.0.1:5555")
        self.sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")
        self.last_emitted_data = {}

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
        total_stocks = len(stock_codes)
        received_stocks = set()
        timeout = max(15000, total_stocks * 500)
        retries = 2

        remaining_codes = set(stock_codes)
        for attempt in range(retries + 1):
            if not remaining_codes:
                break
            self.pub_socket.send_json({"stocks": list(remaining_codes)})
            logger.debug(f"[{caller}] Sent request (attempt {attempt + 1}/{retries + 1}) for {len(remaining_codes)} stocks to port 5556")
            gevent.sleep(1)

            done = False
            while not done:
                try:
                    if self.sub_socket.poll(timeout):
                        data = self.sub_socket.recv_json()
                        if "done" in data and data["done"]:
                            logger.debug(f"[{caller}] Received completion signal")
                            done = True
                        else:
                            logger.debug(f"[{caller}] Received batch data with {len(data)} items")
                            with self.realtime_lock:
                                current_time = time.time()
                                for code, info in data.items():
                                    info['last_updated'] = current_time
                                    self.realtime_data[code] = info
                                    received_stocks.add(code)
                            with app.app_context():
                                self.emit_updates(data)
                    else:
                        logger.warning(f"[{caller}] Timeout waiting for batch data, received {len(received_stocks)}/{total_stocks} stocks")
                        done = True
                except Exception as e:
                    logger.error(f"[{caller}] Error receiving batch: {e}")
                    done = True
            remaining_codes = set(stock_codes) - received_stocks
            if remaining_codes and attempt < retries:
                logger.info(f"[{caller}] Retrying {len(remaining_codes)} missing stocks: {remaining_codes}")

        if remaining_codes:
            logger.error(f"[{caller}] Failed to fetch data for stocks after retries: {remaining_codes}")
                
        logger.info(f"[{caller}] Fetching {total_stocks} stocks via Selenium took {time.time() - start_time:.2f} seconds, received {len(received_stocks)} stocks")


    def emit_updates(self, new_data):
        updates = {}
        with self.realtime_lock:
            for code, data in new_data.items():
                if code not in self.last_emitted_data or self.last_emitted_data[code] != data:
                    updates[code] = data
            if updates:
                try:
                    socketio.emit('realtime_update', updates, namespace='/stocks_realtime')
                    #logger.debug(f"[global] Emitted updates: {updates}")
                    self.last_emitted_data.update(updates)
                except Exception as e:
                    logger.error(f"[global] Error emitting updates: {e}")

    def get_realtime_data(self, stock_codes, source, caller='global'):
        with app.app_context():
            try:
                updated_data = {}
                stock_codes = list(set(stock_codes))
                logger.debug(f"[{caller}] Fetching {source} for {len(stock_codes)} stocks: {stock_codes}")

                if source == 'selenium':
                    current_time = time.time()
                    expired_codes = [
                        code for code in stock_codes
                        if code not in self.realtime_data or
                        (current_time - self.realtime_data[code].get('last_updated', 0) > 300)
                    ]
                    if expired_codes:
                        gevent.spawn(self.fetch_selenium_async, expired_codes, caller)
                    with self.realtime_lock:
                        updated_data = {code: self.realtime_data.get(code, {}) for code in stock_codes}
                    filtered_data = {k: v for k, v in updated_data.items() if v}
                    logger.debug(f"[{caller}] 'selenium' get realtime data: {filtered_data}")
                    return filtered_data

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
                    # 对于 refresh_request，强制更新所选股票，忽略缓存
                    codes_to_fetch = stock_codes if caller == 'refresh_request' else [
                        code for code in stock_codes
                        if code not in self.realtime_data or
                        (time.time() - self.realtime_data[code].get('last_updated', 0) > 300)
                    ]
                    batch_size = DATA_SOURCES[source].get('batch_size', 20)  # NEW - 批量查询支持
                    for i in range(0, len(codes_to_fetch), batch_size):
                        batch = codes_to_fetch[i:i + batch_size]
                        # NEW - 批量查询支持：尝试使用批量查询接口
                        success = False
                        url_template = DATA_SOURCES[source]['backup_url']
                        url = url_template.format(licence=DATA_SOURCES[source]['licence']) + f"?stock_codes={','.join(batch)}"
                        try:
                            response = requests.get(url, timeout=5)
                            response.raise_for_status()
                            data_list = response.json()
                            if isinstance(data_list, list):
                                for data in data_list:
                                    code = data.get('dm', '')
                                    if code in batch:
                                        batch_data = DataAdapter.mairui_adapter(data, code)
                                        updated_data.update(batch_data)
                                success = True
                            else:
                                logger.warning(f"[{caller}] Mairui batch request returned invalid data for {batch}: {data_list}")
                        except requests.RequestException as e:
                            logger.warning(f"[{caller}] Mairui batch request failed for {batch} with {url}: {str(e)}")
                        gevent.sleep(DATA_SOURCES[source]['rate_limit'])  # NEW - 添加批量查询的 rate_limit

                        # NEW - 批量查询支持：如果批量查询失败，回退到单股票查询
                        if not success:
                            logger.info(f"[{caller}] Falling back to single stock query for {batch}")
                            for code in batch:
                                urls = [DATA_SOURCES[source]['main_url'], DATA_SOURCES[source]['backup_url']]
                                single_success = False
                                for url_template in urls:
                                    url = url_template.format(code=code, licence=DATA_SOURCES[source]['licence'])
                                    try:
                                        response = requests.get(url, timeout=5)
                                        response.raise_for_status()
                                        data = response.json()
                                        batch_data = DataAdapter.mairui_adapter(data, code)
                                        updated_data.update(batch_data)
                                        single_success = True
                                        break
                                    except requests.RequestException as e:
                                        logger.warning(f"[{caller}] Mairui single request failed for {code} with {url}: {str(e)}")
                                if not single_success:
                                    logger.error(f"[{caller}] Failed to fetch {code} from mairui after trying all URLs")
                                gevent.sleep(DATA_SOURCES[source]['rate_limit'])  # 每请求一个股票后休眠

                if updated_data:
                    with self.realtime_lock:
                        current_time = time.time()
                        for code, data in updated_data.items():
                            data['last_updated'] = current_time
                            self.realtime_data[code] = data
                    self.emit_updates(updated_data)
                logger.debug(f"[{caller}] {source} returned {len(updated_data)} stocks: ")    # {updated_data}
                return updated_data
            except Exception as e:
                logger.error(f"[{caller}] Error fetching {source} data: {str(e)}", exc_info=True)
                return {}

    def pool_update_task(self):
        logger.info("[global] Starting pool update task")
        print("Starting pool update task")
        while self.running:
            try:
                updated = False
                while True:
                    try:
                        new_stocks = stock_update_queue.get_nowait()
                        caller = new_stocks.get('caller', 'unknown')
                        codes = new_stocks.get('codes', [])
                        logger.debug(f"[global] Retrieved from queue: {codes} from {caller}")
                        print(f"Updated stocks from {caller}: {codes}")
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

                # stocks_pool里面的stocks，如果超过两小时(改为4小时)，还没有接到前端来的更新要求，将从池子里删去。
                with self.realtime_lock:
                    expired = [code for code, info in self.stocks_pool.items() 
                              if time.time() - info['last_updated'] > 14400]
                    for code in expired:
                        del self.stocks_pool[code]
                        if code in self.realtime_data:
                            del self.realtime_data[code]
                    if expired:
                        logger.debug(f"[global] Removed expired stocks: {expired}")
                        print(f"Removed expired stocks: {expired}")
                gevent.sleep(5)
            except Exception as e:
                logger.error(f"[global] Error in pool update task: {str(e)}", exc_info=True)
                gevent.sleep(5)

    def data_update_task(self, source):
        logger.info(f"[global] Starting {source} data update task")
        print(f"Starting {source} data update task")
        while self.running:
            try:
                with self.realtime_lock:
                    self.custom_stocks = [key for key, value in self.stocks_pool.items() if 'custom_stock' in value['sources']]
                    if source == 'mairui':
                        local_stock_codes = [code for code in self.stocks_pool.keys() ] # if code not in self.custom_stocks] # 由mairui更新的股票池
                    elif source == 'tushare':
                        local_stock_codes = [code for code in self.stocks_pool.keys() if code not in self.custom_stocks]
                    elif source == 'selenium':
                        local_stock_codes = [code for code in self.stocks_pool.keys() if code not in self.custom_stocks]
                    else:
                        logger.error(f"[global] Error source {source} ")
                        return

                if not self.stocks_pool:
                    logger.debug(f"[global] Stocks pool is empty for {source}, skipping update")

                if local_stock_codes:
                    with app.app_context():
                        if source == 'selenium':
                            self.get_realtime_data(local_stock_codes, source, caller=f'{source}_task')
                        else:
                            expired_codes = [code for code in local_stock_codes if code not in self.realtime_data or 
                                            (time.time() - self.realtime_data[code].get('last_updated', 0) > 300)]
                            if expired_codes:
                                logger.debug(f"[global] {source} updating {len(expired_codes)} expired stocks")
                                updated_data = self.get_realtime_data(expired_codes, source, caller=f'{source}_task')
                                if updated_data:
                                    with self.realtime_lock:
                                        for code, data in updated_data.items():
                                            data['last_updated'] = time.time()
                                            self.realtime_data[code] = data
                                    self.emit_updates(updated_data)

                in_trading_time = is_trading_time()
                sleep_time = DATA_SOURCES[source]['update_interval']['non_trading_time'] if not in_trading_time else DATA_SOURCES[source]['update_interval']['trading_time']
                
                # 交易日，盘前时间特别处理
                from blueprints.common import is_tradingday
                is_trading_day_flag = is_tradingday(datetime.now().date())
                if is_trading_day_flag:
                    current_time = datetime.now().time()
                    target_time = get_target_time(current_time)
                    
                    if target_time:
                        sleep_until_target(target_time)
                    else:
                        gevent.sleep(sleep_time)
                else:
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
                print(f"Initial stocks pool synced with {len(self.stocks_pool)} stocks")
            
            self.running = True
            socketio.start_background_task(self.pool_update_task)
            self.source_tasks['mairui'] = socketio.start_background_task(self.data_update_task, 'mairui')
            #self.source_tasks['selenium'] = socketio.start_background_task(self.data_update_task, 'selenium')
            #self.source_tasks['tushare'] = socketio.start_background_task(self.data_update_task, 'tushare')
            logger.info("[global] Realtime updater started with multi-source tasks")
            print("Realtime updater started with mairui ,tushare,and selenium tasks")
            gevent.sleep(1)

    def stop(self):
        self.running = False
        self.sub_socket.close()
        self.pub_socket.close()
        self.zmq_context.term()
        logger.info("[global] Realtime updater stopped")
        print("Realtime updater stopped")

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
