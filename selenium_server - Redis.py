import redis
import logging
from playwright.async_api import async_playwright
import yaml
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
import math







logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='C:\\WebApp\\server\\selenium_app.log',
    filemode='a'
)
logger = logging.getLogger(__name__)

with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)
DATA_SOURCES = config['data_sources']
REDIS_CONFIG = config['queues']['redis']

redis_client = redis.Redis(
    host=REDIS_CONFIG['host'],
    port=REDIS_CONFIG['port'],
    db=REDIS_CONFIG['db']
)

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
        timezone_id="Asia/Shanghai"
    )
    page = await context.new_page()
    
    try:
        logger.debug(f"Navigating to {url} for {code}")
        goto_timeout = DATA_SOURCES['selenium'].get('timeouts', {}).get('goto', 30000)
        selector_timeout = DATA_SOURCES['selenium'].get('timeouts', {}).get('selector', 10000)
        await page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout)
        table = await page.wait_for_selector("div.sider_brief table.t1", timeout=selector_timeout)
        if not table:
            logger.warning(f"Table 'div.sider_brief table.t1' not found for {code} at {url}")
            return updated_data

        rows = await table.query_selector_all("tr")
        data = {}
        for row in rows:
            tds = await row.query_selector_all("td")
            for td in tds:
                text = await td.inner_text()
                key_value = text.split("：")
                if len(key_value) == 2:
                    key, value = key_value
                    data[key.strip()] = value.strip()
        
        price_str = data.get("最新", "0")
        prev_close_str = data.get("昨收", "0")
        try:
            price = float(price_str.replace(',', '')) if price_str else 0
            prev_close = float(prev_close_str.replace(',', '')) if prev_close_str else 0
            updated_data[code] = {
                'RealtimePrice': price,
                'RealtimeChange': round(((price - prev_close) / prev_close * 100) if prev_close != 0 else 0, 2)
            }
            logger.debug(f"Parsed data for {code}: {updated_data[code]}")
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse price data for {code}: {e}, raw data: {data}")
        return updated_data
    except Exception as e:
        logger.error(f"Error fetching {code} from {url}: {e}")
        return {}
    finally:
        try:
            await page.close()
            await context.close()
        except Exception as close_err:
            logger.warning(f"Error closing page/context for {code}: {close_err}")

async def fetch_stock_batch(batch_codes, url_template):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        tasks = [fetch_one_stock(browser, code, url_template) for code in batch_codes]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()
    
    valid_results = {}
    missing_codes = set(batch_codes)
    for result in results:
        if isinstance(result, dict):
            valid_results.update(result)
            for code in result:
                missing_codes.discard(code)
        elif isinstance(result, Exception):
            logger.error(f"Batch task failed with exception: {result}")
    
    for code, data in valid_results.items():
        if not data or 'RealtimePrice' not in data:
            logger.warning(f"Invalid data for {code}: {data}")
            del valid_results[code]
            missing_codes.add(code)
    
    if missing_codes:
        logger.warning(f"Missing or invalid data in batch for codes: {missing_codes}")
    return valid_results

def process_batch_sync(batch_codes, url_template):
    return asyncio.run(fetch_stock_batch(batch_codes, url_template))

async def fetch_stock_data(stock_codes, url_template):
    batch_size = 50
    num_batches = math.ceil(len(stock_codes) / batch_size)
    batches = [stock_codes[i:i + batch_size] for i in range(0, len(stock_codes), batch_size)]
    max_workers = min(10, max(1, len(stock_codes) // 20))
    
    logger.debug(f"Processing {len(stock_codes)} stocks in {num_batches} batches with {max_workers} workers")
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_batch_sync, batch, url_template) for batch in batches]
        for idx, future in enumerate(futures, 1):
            retries = 2
            for attempt in range(retries + 1):
                try:
                    data = future.result(timeout=60)
                    results.update(data)
                    logger.debug(f"Processed batch {idx}/{num_batches} with {len(data)} items")
                    break
                except Exception as e:
                    if attempt < retries:
                        logger.warning(f"Batch {idx} failed on attempt {attempt + 1}/{retries + 1}: {e}, retrying...")
                    else:
                        logger.error(f"Batch {idx} failed after {retries + 1} attempts: {e}")
    return results

async def main():
    url_template = DATA_SOURCES['selenium']['url_template']
    logger.info("Selenium Server started with Redis queue...")
    print("Selenium Server started with Redis queue...")
    
    while True:
        try:
            task = redis_client.rpop(REDIS_CONFIG['tasks_queue'])
            if task:
                task_data = json.loads(task)
                stock_codes = task_data["stocks"]
                task_id = task_data["task_id"]
                logger.debug(f"Received task: {task_data}")
                print(f"Processing task: {task_id} with {len(stock_codes)} stocks")
                
                result = await fetch_stock_data(stock_codes, url_template)
                result_msg = {
                    "task_id": task_id,
                    "data": result,
                    "status": "success" if result else "failed"
                }
                redis_client.lpush(REDIS_CONFIG['results_queue'], json.dumps(result_msg))
                redis_client.hdel("pending_tasks", task_id)  # 完成后移除任务
                logger.debug(f"Pushed result and removed task {task_id}: {result_msg}")
                print(f"Completed task: {task_id}")
            else:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())