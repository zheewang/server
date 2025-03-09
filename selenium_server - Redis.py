import redis
import logging
from playwright.async_api import async_playwright
import yaml
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
import math
import time

# 设置日志，使用 UTF-8 编码
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='C:\\WebApp\\server\\selenium_app.log',
    filemode='a',
    encoding='utf-8'
)
logger = logging.getLogger(__name__)

with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)
DATA_SOURCES = config['data_sources']
REDIS_CONFIG = config['queues']['redis']

# Redis 连接池
redis_pool = redis.ConnectionPool(host=REDIS_CONFIG['host'], port=REDIS_CONFIG['port'], db=REDIS_CONFIG['db'])
redis_client = redis.Redis(connection_pool=redis_pool)

# 本地缓存
realtime_data = {}
data_lock = asyncio.Lock()

def get_stock_prefix(stock_code):
    first_char = stock_code[0]
    first_digit = int(first_char)
    if first_digit in {0, 3}:
        return 'sz'
    elif first_digit == 6:
        return 'sh'
    return ''

async def fetch_one_stock(browser, code, url_template):
    updated_data = {}
    prefixed_code = f"{get_stock_prefix(code)}{code}"
    url = url_template.format(code=prefixed_code)
    
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9", "Content-Type": "text/html; charset=utf-8"}
    )
    page = await context.new_page()
    
    try:
        logger.debug(f"Navigating to {url} for {code}")
        await page.goto(url, wait_until="domcontentloaded", timeout=DATA_SOURCES['selenium']['timeouts']['goto'])
        table = await page.wait_for_selector("div.sider_brief table.t1", timeout=DATA_SOURCES['selenium']['timeouts']['selector'])
        if not table:
            logger.warning(f"Table not found for {code}")
            return updated_data

        rows = await table.query_selector_all("tr")
        data = {}
        for row in rows:
            tds = await row.query_selector_all("td")
            for td in tds:
                text = await td.inner_text()
                text = text.encode().decode('utf-8', errors='replace')
                key_value = text.split("：")
                if len(key_value) == 2:
                    key, value = key_value[0].strip(), key_value[1].strip()
                    if key:
                        data[key] = value
        
        logger.debug(f"Raw data for {code}: {data}")
        price_str = data.get("最新", "0")
        prev_close_str = data.get("昨收", "0")
        
        try:
            price = float(price_str.replace(',', '')) if price_str and price_str != '-' else 0
            prev_close = float(prev_close_str.replace(',', '')) if prev_close_str and prev_close_str != '-' else 0
            updated_data[code] = {
                'RealtimePrice': price,
                'RealtimeChange': round(((price - prev_close) / prev_close * 100) if prev_close != 0 else 0, 2),
                'last_updated': time.time()
            }
        except (ValueError, TypeError) as e:
            logger.warning(f"Parse error for {code}: {e}, price_str: '{price_str}', prev_close_str: '{prev_close_str}'")
            updated_data[code] = {'RealtimePrice': 0, 'RealtimeChange': 0, 'last_updated': time.time()}
        return updated_data
    except Exception as e:
        logger.error(f"Error fetching {code}: {e}")
        return {}
    finally:
        await page.close()
        await context.close()

async def fetch_stock_batch(batch_codes, url_template):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        tasks = [fetch_one_stock(browser, code, url_template) for code in batch_codes]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()
    
    valid_results = {}
    async with data_lock:
        for result in results:
            if isinstance(result, dict):
                valid_results.update(result)
                for code, data in result.items():
                    if data and 'RealtimePrice' in data:
                        realtime_data[code] = data
                    else:
                        logger.warning(f"Invalid data for {code}: {data}")
    return valid_results

def process_batch_sync(batch_codes, url_template):
    return asyncio.run(fetch_stock_batch(batch_codes, url_template))

async def fetch_stock_data(stock_codes, url_template):
    batch_size = 50
    current_time = time.time()
    cached_data = {}
    to_fetch = []
    
    async with data_lock:
        for code in stock_codes:
            if (code in realtime_data and 
                'last_updated' in realtime_data[code] and 
                current_time - realtime_data[code]['last_updated'] < 180):  # 3 分钟缓存
                cached_data[code] = realtime_data[code]
                logger.debug(f"Using cached data for {code}: {cached_data[code]}")
            else:
                to_fetch.append(code)
    
    results = cached_data
    if to_fetch:
        batches = [to_fetch[i:i + batch_size] for i in range(0, len(to_fetch), batch_size)]
        max_workers = min(10, max(1, len(to_fetch) // 20))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_batch_sync, batch, url_template) for batch in batches]
            for future in futures:
                try:
                    data = future.result(timeout=60)
                    results.update(data)
                except Exception as e:
                    logger.error(f"Batch failed: {e}")
    return results

async def clean_expired_data():
    while True:
        current_time = time.time()
        async with data_lock:
            expired = [code for code, data in realtime_data.items() if current_time - data['last_updated'] > 3600]
            for code in expired:
                del realtime_data[code]
        if expired:
            logger.debug(f"Cleaned {len(expired)} expired entries: {expired}")
        await asyncio.sleep(3600)

async def main():
    url_template = DATA_SOURCES['selenium']['url_template']
    logger.info("Selenium Server started with Redis queue...")
    print("Selenium Server started with Redis queue...")
    asyncio.create_task(clean_expired_data())

    while True:
        try:
            # 优先处理高优先级队列
            task = redis_client.rpop(REDIS_CONFIG['tasks_queue_high']) or redis_client.rpop(REDIS_CONFIG['tasks_queue_low'])
            if task:
                task_data = json.loads(task.decode('utf-8') if isinstance(task, bytes) else task)
                task_id = task_data["task_id"]
                if redis_client.sismember(REDIS_CONFIG['processed_tasks_set'], task_id):
                    logger.debug(f"Skipping duplicate task: {task_id}")
                    continue
                
                stock_codes = task_data["stocks"]
                logger.debug(f"Processing task {task_id} with {len(stock_codes)} stocks")
                print(f"Processing task: {task_id}")
                
                result = await fetch_stock_data(stock_codes, url_template)
                result_msg = {"task_id": task_id, "data": result, "status": "success" if result else "failed"}
                redis_client.lpush(REDIS_CONFIG['results_queue'], json.dumps(result_msg))
                redis_client.hdel("pending_tasks", task_id)
                redis_client.sadd(REDIS_CONFIG['processed_tasks_set'], task_id)
                logger.debug(f"Completed task {task_id}")
                print(f"Completed task: {task_id}")
            else:
                await asyncio.sleep(1)
            # 监控队列长度
            logger.info(f"High queue: {redis_client.llen(REDIS_CONFIG['tasks_queue_high'])}, Low queue: {redis_client.llen(REDIS_CONFIG['tasks_queue_low'])}, Results: {redis_client.llen(REDIS_CONFIG['results_queue'])}")
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())