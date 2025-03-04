from flask_socketio import emit
import gevent.lock
import gevent.queue
from app_init import app, db, socketio
import tushare as ts
import gevent
import time
import requests
import logging
import yaml
from datetime import datetime, time as dt_time
from blueprints.custom_stock import read_stock_codes
from multiprocessing import Process, Queue,Manager

logger = app.logger
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)
DATA_SOURCES = config['data_sources']
ts.set_token(DATA_SOURCES['tushare']['token'])
pro = ts.pro_api()

stocks_pool = {}
stocks_pool_lock = gevent.lock.Semaphore()
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
        self.manager = Manager()
        self.stocks_pool = self.manager.dict()  # 使用共享字典
        self.realtime_data = self.manager.dict()  # 共享实时数据
        self.realtime_lock = gevent.lock.Semaphore()
        self.running = False
        self.selenium_queue = gevent.queue.Queue()  # 让 Selenium 进程返回数据
        self.global_update_queue = gevent.queue.Queue()  # 让 global_updater 发送请求
        self.selenium_process = None

        self.source_tasks = {}

    def get_stock_suffix(self, stock_code):
        first_char = stock_code[0]
        first_digit = int(first_char)
        if first_digit in {0, 3}:
            return '.SZ'
        elif first_digit == 6:
            return '.SH'
        return ''

    def get_realtime_data(self, stock_codes, source, caller='global'):
        with app.app_context():
            try:
                updated_data = {}
                stock_codes = list(set(stock_codes))
                logger.debug(f"[{caller}] Fetching {source} for {len(stock_codes)} stocks: {stock_codes}")

                if source == 'selenium':
                    self.global_update_queue.put(stock_codes)  # 请求 Selenium 更新
                    logger.debug(f"[{caller}] Sent {stock_codes} to Selenium process")

                    # 轮询等待 Selenium 数据，异步检查
                    updated_data = {}
                    start_time = time.time()
                    while time.time() - start_time < 10:  # 轮询 10 秒
                        try:
                            batch_data = self.selenium_queue.get_nowait()  # 非阻塞获取数据
                            updated_data.update(batch_data)
                            if updated_data:  # 数据已返回，立即结束
                                break
                        except gevent.queue.Empty:
                            gevent.sleep(0.5)  # 短暂休眠，避免高 CPU 占用

                    if updated_data:
                        with self.realtime_lock:
                            self.realtime_data.update(updated_data)
                        return updated_data
                    else:
                        logger.warning(f"[{caller}] No Selenium data received within timeout")
                        return {}
                    gevent.sleep(60)

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
                
                logger.debug(f"[{caller}] {source} updated_data: {updated_data}")
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
                gevent.sleep(5)

    def selenium_listener_task(self):
        """监听 Selenium 进程发送的数据"""
        logger.info("[global] Starting Selenium listener task")
        while self.running:
            try:
                updated_data = self.selenium_queue.get_nowait()
                if updated_data:
                    with self.realtime_lock:
                        self.realtime_data.update(updated_data)
                    with app.app_context():
                        socketio.emit('realtime_update', updated_data, namespace='/stocks_realtime')
                    logger.debug(f"[global] Selenium data received and emitted: {updated_data}")
                gevent.sleep(1)  # 短暂休眠，避免高 CPU 占用
            except gevent.queue.Empty:
                gevent.sleep(1)
            except Exception as e:
                logger.error(f"[global] Error in Selenium listener task: {str(e)}", exc_info=True)
                gevent.sleep(5)

    def data_update_task(self, source):
        logger.info(f"[global] Starting {source} data update task")
        while self.running:
            try:
                with stocks_pool_lock:
                    if source == 'mairui':
                        local_stock_codes = [code for code in stocks_pool.keys() if code in self.custom_stocks]
                    elif source == 'tushare':
                        local_stock_codes = [code for code in stocks_pool.keys() if code not in self.custom_stocks]
                    else:
                        local_stock_codes = []  # Selenium 在独立进程中处理

                if local_stock_codes:
                    with app.app_context():
                        logger.debug(f"[global] {source} updating {len(local_stock_codes)} stocks")
                        updated_data = self.get_realtime_data(local_stock_codes, source, caller=f'{source}_task')
                        if updated_data:
                            with self.realtime_lock:
                                self.realtime_data.update(updated_data)
                            socketio.emit('realtime_update', updated_data, namespace='/stocks_realtime')
                            logger.debug(f"[global] {source} emitted: {updated_data}")

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
                logger.debug(f"[global] Initial stocks_pool: {stocks_pool}")
                
            self.running = True

            socketio.start_background_task(self.pool_update_task)
            socketio.start_background_task(self.selenium_listener_task)
            #self.source_tasks['tushare'] = socketio.start_background_task(self.data_update_task, 'tushare')
            self.source_tasks['mairui'] = socketio.start_background_task(self.data_update_task, 'mairui')
            self.selenium_process = Process(
                    target=selenium_process, args=(self.selenium_queue, self.global_update_queue))
            self.selenium_process.start()
            logger.info("[global] Realtime updater started with multi-source tasks and Selenium process")
            gevent.sleep(1)

    def stop(self):
        self.running = False
        if self.selenium_process:
            self.selenium_process.terminate()
            self.selenium_process.join()
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

##### **Selenium 独立进程逻辑**
def selenium_process(selenium_queue, global_update_queue):
    import asyncio
    from playwright.async_api import async_playwright

    async def main():
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        
        while True:
            try:
                stock_codes = global_update_queue.get(timeout=5)  # 等待 global_updater 任务
                if not stock_codes:
                    continue

                updated_data = {}
                page = await browser.new_page()
                for code in stock_codes:
                    prefixed_code = f"{get_stock_prefix(code)}{code}"
                    url = DATA_SOURCES['selenium']['url_template'].format(prefixed_code)

                    await page.goto(url, wait_until="domcontentloaded")
                    await page.wait_for_selector("div.sider_brief table.t1", timeout=5000)
                    table = await page.query_selector("div.sider_brief table.t1")
                    rows = await table.query_selector_all("tr")

                    data = {"Stock Code": code}
                    for row in rows:
                        tds = await row.query_selector_all("td")
                        for td in tds:
                            text = await td.inner_text()
                            key_value = text.split("：")
                            if len(key_value) == 2:
                                key, value = key_value
                                data[key.strip()] = value.strip()

                    batch_data = DataAdapter.selenium_adapter(data)
                    updated_data.update(batch_data)

                await page.close()

                if updated_data:
                    selenium_queue.put_nowait(updated_data)  # 非阻塞返回数据,把数据推送回去
                    logger.debug(f"Selenium process sent data: {updated_data}")
            except Exception as e:
                logger.error(f"Selenium process error: {e}")
    
    asyncio.run(main())

    '''
    async def main():
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        logger.info("Selenium process started with Playwright")

        try:
            while True:
                with stocks_pool_lock:
                    local_stock_codes = list(stocks_pool.keys())  # Selenium 处理所有股票
                if local_stock_codes:
                    updated_data = await get_selenium_data_async(local_stock_codes, browser)
                    if updated_data:
                        queue.put(updated_data)
                        logger.debug(f"Selenium process sent data: {updated_data}")
                
                # 检查是否需要重启
                current_time = time.time()
                if (current_time - selenium_process.last_restart_time) >= 3600:  # 每小时重启
                    await browser.close()
                    browser = await playwright.chromium.launch(headless=True)
                    selenium_process.last_restart_time = current_time
                    logger.info("Playwright browser restarted in Selenium process")

                in_trading_time = is_trading_time()
                sleep_time = DATA_SOURCES['selenium']['update_interval']['non_trading_time'] if not in_trading_time else DATA_SOURCES['selenium']['update_interval']['trading_time']
                await asyncio.sleep(sleep_time)
        except KeyboardInterrupt:
            await browser.close()
            await playwright.stop()
            logger.info("Selenium process stopped")

    selenium_process.last_restart_time = time.time()  # 静态属性记录重启时间
    asyncio.run(main())

    '''