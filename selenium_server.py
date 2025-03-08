import zmq
import logging
from playwright.async_api import async_playwright
import yaml
import asyncio
import zmq.asyncio
from concurrent.futures import ThreadPoolExecutor
import math

# 设置日志级别为 DEBUG，输出到文件
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='C:\\WebApp\\server\\selenium_app.log',
    filemode='a'
)

logger = logging.getLogger(__name__)

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
        print(f"Fetching data for stock {code} from {url}")
        logger.debug(f"Navigating to {url} for {code}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        table = await page.wait_for_selector("div.sider_brief table.t1", timeout=10000)
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
    if missing_codes:
        logger.warning(f"Missing data in batch for codes: {missing_codes}")
    return valid_results

def process_batch_sync(batch_codes, url_template):
    return asyncio.run(fetch_stock_batch(batch_codes, url_template))

async def fetch_stock_data(stock_codes, url_template, socket):
    batch_size = 50
    num_batches = math.ceil(len(stock_codes) / batch_size)
    batches = [stock_codes[i:i + batch_size] for i in range(0, len(stock_codes), batch_size)]

    logger.debug(f"Processing {len(stock_codes)} stocks in {num_batches} batches")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_batch_sync, batch, url_template) for batch in batches]
        for idx, future in enumerate(futures, 1):
            try:
                data = future.result()
                await socket.send_json(data)
                logger.debug(f"Sent batch {idx}/{num_batches} data: {data}")
            except Exception as e:
                logger.error(f"Batch {idx} processing failed: {e}")
    
    await socket.send_json({"done": True})
    logger.debug("Sent completion signal: {'done': True}")

async def main():
    context = zmq.asyncio.Context()
    socket = context.socket(zmq.PUB)
    socket.bind("tcp://*:5555")
    logger.info("Selenium Server (PUB) started on port 5555...")
    print("Selenium Server started on port 5555...")  # 控制台输出启动信息

    sub_socket = context.socket(zmq.SUB)
    sub_socket.bind("tcp://*:5556")
    sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")

    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    DATA_SOURCES = config['data_sources']
    url_template = DATA_SOURCES['selenium']['url_template']

    logger.debug("Server entering main loop")
    while True:
        try:
            logger.debug("Waiting for request on port 5556")
            message = await sub_socket.recv_json()
            stock_codes = message.get("stocks", [])
            # 显示接收到的股票代码
            print(f"Received request for stocks: {stock_codes}")
            logger.debug(f"Received request for stocks: {stock_codes}")
            await fetch_stock_data(stock_codes, url_template, socket)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            await socket.send_json({})

if __name__ == "__main__":
    asyncio.run(main())