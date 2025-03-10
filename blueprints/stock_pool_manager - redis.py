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
import redis
import uuid
import json
from dateutil.parser import parse

logger = app.logger
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)
DATA_SOURCES = config['data_sources']
REDIS_CONFIG = config['queues']['redis']

ts.set_token(DATA_SOURCES['tushare']['token'])
pro = ts.pro_api()

stock_update_queue = gevent.queue.Queue()

def is_trading_time():
    now = datetime.now()
    weekday = now.weekday()
    if weekday > 4:
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

class RealtimeUpdater:
    def __init__(self):
        self.stocks_pool = {}
        self.realtime_data = {}
        self.realtime_lock = gevent.lock.Semaphore()
        self.running = False
        self.source_tasks = {}
        self.custom_stocks = []
        self.last_emitted_data = {}
        self.redis_client = redis.Redis(
            connection_pool=redis.ConnectionPool(host=REDIS_CONFIG['host'], 
                                                 port=REDIS_CONFIG['port'], 
                                                 db=REDIS_CONFIG['db']))

    def get_stock_suffix(self, stock_code):
        first_char = stock_code[0]
        first_digit = int(first_char)
        if first_digit in {0, 3}:
            return '.SZ'
        elif first_digit == 6:
            return '.SH'
        return ''

    def emit_updates(self, new_data):
        updates = {}
        with self.realtime_lock:
            for code, data in new_data.items():
                if code not in self.last_emitted_data or self.last_emitted_data[code] != data:
                    updates[code] = data
            if updates:
                try:
                    socketio.emit('realtime_update', updates, namespace='/stocks_realtime')
                    logger.debug(f"[global] Emitted updates: {updates}")
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
                    expired_codes = [code for code in stock_codes if code not in self.realtime_data or (current_time - self.realtime_data[code].get('last_updated', 0) > 120)]
                    if expired_codes:
                        task = {
                            "task_id": str(uuid.uuid4()),
                            "stocks": expired_codes,
                            "timestamp": datetime.now().isoformat(),
                            "priority": 2 if caller == "custom_stock" else 1  # 示例：custom_stock 高优先级
                        }
                        queue_name = REDIS_CONFIG['tasks_queue_high'] if task["priority"] > 1 else REDIS_CONFIG['tasks_queue_low']
                        self.redis_client.lpush(queue_name, json.dumps(task))
                        self.redis_client.hset("pending_tasks", task["task_id"], json.dumps(task))
                        logger.debug(f"[{caller}] Pushed task to {queue_name}: {task}")
                    with self.realtime_lock:
                        updated_data = {code: self.realtime_data.get(code, {}) for code in stock_codes}
                    return {k: v for k, v in updated_data.items() if v}

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
                    for i in range(0, len(codes_to_fetch), batch_size):
                        batch = codes_to_fetch[i:i + batch_size]
                        for code in batch:
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
                                    logger.warning(f"[{caller}] Mairui request failed for {code} with {url}: {str(e)}")
                            if not success:
                                logger.error(f"[{caller}] Failed to fetch {code} from mairui after trying all URLs")
                            gevent.sleep(DATA_SOURCES[source]['rate_limit'])

                if updated_data:
                    with self.realtime_lock:
                        current_time = time.time()
                        for code, data in updated_data.items():
                            data['last_updated'] = current_time
                            self.realtime_data[code] = data
                    self.emit_updates(updated_data)
                logger.debug(f"[{caller}] {source} returned {len(updated_data)} stocks: {updated_data}")
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
                            logger.debug(f"[global] Updated stocks_pool with {len(self.stocks_pool)} items")
                        updated = True
                    except gevent.queue.Empty:
                        break

                with self.realtime_lock:
                    expired = [code for code, info in self.stocks_pool.items() 
                              if time.time() - info['last_updated'] > 7200]
                    for code in expired:
                        del self.stocks_pool[code]
                        if code in self.realtime_data:
                            del self.realtime_data[code]
                    if expired:
                        logger.debug(f"[global] Removed {len(expired)} expired stocks")
                        print(f"Removed expired stocks: {expired}")
                gevent.sleep(5)
            except Exception as e:
                logger.error(f"[global] Error in pool update task: {str(e)}", exc_info=True)
                gevent.sleep(5)

    def check_pending_tasks(self):
        """检查超时的待处理任务并重新入队"""
        current_time = datetime.now()
        pending_tasks = self.redis_client.hgetall("pending_tasks")
        for task_id, task_json in pending_tasks.items():
            task = json.loads(task_json.decode('utf-8') if isinstance(task_json, bytes) else task_json)
            task_time = parse(task["timestamp"])
            if (current_time - task_time).total_seconds() > 300:  # 5 分钟超时
                logger.warning(f"Task {task_id} timed out, requeueing...")
                task["timestamp"] = current_time.isoformat() # 更新时间戳
                queue_name = REDIS_CONFIG['tasks_queue_high'] if task["priority"] > 1 else REDIS_CONFIG['tasks_queue_low']
                self.redis_client.lpush(queue_name, json.dumps(task))
                self.redis_client.hset("pending_tasks", task_id, json.dumps(task)) # 更新时间戳
                logger.debug(f"Requeued task: {task}")

    def data_update_task(self, source):
        logger.info(f"[global] Starting {source} data update task")
        print(f"Starting {source} data update task")
        while self.running:
            try:
                with self.realtime_lock:
                    self.custom_stocks = [key for key, value in self.stocks_pool.items() if 'custom_stock' in value['sources']]
                    if source == 'mairui':
                        local_stock_codes = [code for code in self.stocks_pool.keys() if code in self.custom_stocks]
                    elif source == 'tushare':
                        local_stock_codes = [code for code in self.stocks_pool.keys() if code not in self.custom_stocks]
                    elif source == 'selenium':
                        local_stock_codes = [code for code in self.stocks_pool.keys() if code not in self.custom_stocks]
                    else:
                        logger.error(f"[global] Error source {source}")
                        return

                if not self.stocks_pool:
                    logger.debug(f"[global] Stocks pool is empty for {source}, skipping update")

                if local_stock_codes:
                    with app.app_context():
                        if source == 'selenium':
                            self.get_realtime_data(local_stock_codes, source, caller=f'{source}_task')
                            result = self.redis_client.rpop(REDIS_CONFIG['results_queue'])
                            if result:
                                result_data = json.loads(result.decode('utf-8') if isinstance(result, bytes) else result)
                                if result_data["status"] == "success":
                                    with self.realtime_lock:
                                        current_time = time.time()
                                        for code, info in result_data["data"].items():
                                            info['last_updated'] = current_time
                                            self.realtime_data[code] = info
                                    self.emit_updates(result_data["data"])
                                else:
                                    logger.warning(f"Task {result_data['task_id']} failed")
                            # 检查超时任务
                            self.check_pending_tasks()
                        else:
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
                                    self.emit_updates(updated_data)

                in_trading_time = is_trading_time()
                sleep_time = DATA_SOURCES[source]['update_interval']['non_trading_time'] if not in_trading_time else DATA_SOURCES[source]['update_interval']['trading_time']
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
            self.source_tasks['selenium'] = socketio.start_background_task(self.data_update_task, 'selenium')
            logger.info("[global] Realtime updater started with multi-source tasks")
            print("Realtime updater started with mairui and selenium tasks")
            gevent.sleep(1)

    def stop(self):
        self.running = False
        self.redis_client.close()
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