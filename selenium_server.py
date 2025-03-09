import zmq
import logging
from playwright.async_api import async_playwright
import yaml
import asyncio
import zmq.asyncio
from concurrent.futures import ThreadPoolExecutor
import math
import time

# 设置日志级别为 DEBUG，输出到文件
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='C:\\WebApp\\server\\selenium_app.log',
    filemode='a',
    encoding='utf-8'  # 强制 UTF-8 编码
)

logger = logging.getLogger(__name__)

with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)
DATA_SOURCES = config['data_sources']

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
    
    # 设置明确的语言和编码头
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        extra_http_headers={
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Content-Type": "text/html; charset=utf-8"
        }
    )
    page = await context.new_page()
    
    try:
        print(f"Fetching data for stock {code} from {url}")
        logger.debug(f"Navigating to {url} for {code}")
        goto_timeout = DATA_SOURCES['selenium'].get('timeouts', {}).get('goto', 30000)
        selector_timeout = DATA_SOURCES['selenium'].get('timeouts', {}).get('selector', 10000)
        await page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout)
        
        # 检查页面内容和编码
        html_content = await page.content()
        logger.debug(f"Page content preview for {code}: {html_content[:200]}")  # 记录前 200 字符
        
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
                # 确保文本正确解码
                try:
                    text = text.encode().decode('utf-8', errors='replace')  # 强制 UTF-8，替换无效字符
                except Exception as decode_err:
                    logger.warning(f"Decoding error for {code}: {decode_err}, raw text: {text}")
                    continue
                
                key_value = text.split("：")
                if len(key_value) == 2:
                    key, value = key_value
                    key = key.strip()
                    value = value.strip()
                    if key:  # 跳过空键
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
            logger.debug(f"Parsed data for {code}: {updated_data[code]}")
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse price data for {code}: {e}, price_str: '{price_str}', prev_close_str: '{prev_close_str}', raw data: {data}")
            updated_data[code] = {
                'RealtimePrice': 0,
                'RealtimeChange': 0,
                'last_updated': time.time()
            }
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
    async with data_lock:
        for result in results:
            if isinstance(result, dict):
                valid_results.update(result)
                for code in result:
                    missing_codes.discard(code)
        
        for code, data in valid_results.items():
            if not data or 'RealtimePrice' not in data:
                logger.warning(f"Invalid data for {code}: {data}")
                del valid_results[code]
                missing_codes.add(code)
            else:
                realtime_data[code] = data
    
    if missing_codes:
        logger.warning(f"Missing or invalid data in batch for codes: {missing_codes}")
    return valid_results

def process_batch_sync(batch_codes, url_template):
    return asyncio.run(fetch_stock_batch(batch_codes, url_template))

async def fetch_stock_data(stock_codes, url_template, socket):
    batch_size = 30
    num_batches = math.ceil(len(stock_codes) / batch_size)
    max_workers = min(10, max(1, len(stock_codes) // 20))
    
    logger.debug(f"Processing {len(stock_codes)} stocks in {num_batches} batches with {max_workers} workers")
    
    current_time = time.time()
    cached_data = {}
    to_fetch = []
    async with data_lock:
        for code in stock_codes:
            if (code in realtime_data and 
                'last_updated' in realtime_data[code] and 
                current_time - realtime_data[code]['last_updated'] < 300):
                cached_data[code] = realtime_data[code]
                logger.debug(f"Using cached data for {code}: {cached_data[code]}")
            else:
                to_fetch.append(code)
    
    if cached_data:
        await socket.send_json(cached_data)
        logger.debug(f"Sent cached data for {len(cached_data)} stocks: {cached_data}")
    
    if to_fetch:
        batches = [to_fetch[i:i + batch_size] for i in range(0, len(to_fetch), batch_size)]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_batch_sync, batch, url_template) for batch in batches]
            for idx, future in enumerate(futures, 1):
                retries = 2
                for attempt in range(retries + 1):
                    try:
                        data = future.result(timeout=60)
                        await socket.send_json(data)
                        logger.debug(f"Sent batch {idx}/{num_batches} data: {data}")
                        break
                    except Exception as e:
                        if attempt < retries:
                            logger.warning(f"Batch {idx} failed on attempt {attempt + 1}/{retries + 1}: {e}, retrying...")
                        else:
                            logger.error(f"Batch {idx} failed after {retries + 1} attempts: {e}")
    
    await socket.send_json({"done": True})
    logger.debug("Sent completion signal: {'done': True}")

async def clean_expired_data():
    while True:
        current_time = time.time()
        expired = []
        async with data_lock:
            expired = [code for code, data in realtime_data.items() 
                      if current_time - data['last_updated'] > 3600]
            for code in expired:
                del realtime_data[code]
        if expired:
            logger.debug(f"Cleaned {len(expired)} expired entries from realtime_data: {expired}")
        await asyncio.sleep(3600)

async def main():
    context = zmq.asyncio.Context()
    socket = context.socket(zmq.PUB)
    socket.bind("tcp://*:5555")
    logger.info("Selenium Server (PUB) started on port 5555...")
    print("Selenium Server started on port 5555...")

    sub_socket = context.socket(zmq.SUB)
    sub_socket.bind("tcp://*:5556")
    sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")

    url_template = DATA_SOURCES['selenium']['url_template']

    asyncio.create_task(clean_expired_data())

    logger.debug("Server entering main loop")
    while True:
        try:
            logger.debug("Waiting for request on port 5556")
            message = await sub_socket.recv_json()
            stock_codes = message.get("stocks", [])
            print(f"Received request for stocks: {stock_codes}")
            logger.debug(f"Received request for stocks: {stock_codes}")
            await fetch_stock_data(stock_codes, url_template, socket)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            await socket.send_json({})

if __name__ == "__main__":
    asyncio.run(main())