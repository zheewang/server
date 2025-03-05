import zmq
import logging
from playwright.async_api import async_playwright
import yaml
import asyncio
import zmq.asyncio


# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SeleniumClient")


def get_stock_prefix(stock_code):
    first_char = stock_code[0]
    first_digit = int(first_char)
    if first_digit in {0, 3}:
        return 'sz'
    elif first_digit == 6:
        return 'sh'
    return ''

async def fetch_stock_data_optional(stock_codes, url_template, batch_size=10):
    """Fetch stock data in batches"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        all_results = {}
        for i in range(0, len(stock_codes), batch_size):
            batch = stock_codes[i:i + batch_size]
            print(f"Processing batch: {batch}")
            
            semaphore = asyncio.Semaphore(batch_size)  # Limit to `batch_size` concurrent requests
            tasks = [fetch_one_stock(browser, code, url_template, semaphore) for code in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            valid_results = [r for r in results if isinstance(r, dict) and "Stock Code" in r]
            all_results.update({r["Stock Code"]: r for r in valid_results})

        await browser.close()
    return all_results


async def fetch_stock_data(stock_codes, url_template, max_concurrency=10):
    """Fetch stock data with concurrency control"""
    semaphore = asyncio.Semaphore(max_concurrency)  # Limit to `max_concurrency` concurrent requests
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)

        tasks = [fetch_one_stock(browser, code, url_template) for code in stock_codes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        await browser.close()

    # Merge the list of dictionaries into a single dictionary using dictionary comprehension
    valid_results = {key: value for item in results for key, value in item.items()}
    return valid_results


async def fetch_one_stock(browser, code, url_template):
    """爬取单个股票数据"""
    # Create a new context and page for each request
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        locale="zh-CN",
        timezone_id="Asia/Shanghai"
    )
    page = await context.new_page()
    updated_data = {}
    prefixed_code = f"{get_stock_prefix(code)}{code}"
    url = url_template.format(code=prefixed_code)
    
    try:
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_selector("div.sider_brief table.t1", timeout=10000)
        
        table = await page.query_selector("div.sider_brief table.t1")
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
        
        # 与 stock_pool_manager.py 的 DataAdapter.selenium_adapter 一致
        price_str = data.get("最新", "0")
        prev_close_str = data.get("昨收", "0")
        try:
            price = float(price_str.replace(',', '')) if price_str else 0
            prev_close = float(prev_close_str.replace(',', '')) if prev_close_str else 0
            updated_data[code] = {
                'RealtimePrice': price,
                'RealtimeChange': round(((price - prev_close) / prev_close * 100) if prev_close != 0 else 0, 2)
            }
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse data for {code}: {e}")


        return updated_data

    except Exception as e:
        print(f"Error fetching {code}: {e}")
        return {} 
    finally:
        # Close the page and context after the request is done
        await page.close()
        await context.close()

async def main():
    context = zmq.asyncio.Context()
    socket = context.socket(zmq.REP)  # REP (Reply) 模式
    socket.bind("tcp://*:5556")  # 绑定 5555 端口，等待请求

    print("Selenium Server started on port 5555..")

    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)

    DATA_SOURCES = config['data_sources']
    url_template=DATA_SOURCES['selenium']['url_template']

    while True:
        try:
            message = await socket.recv_json()  # 异步接收请求 # 接收 JSON 格式的请求
            stock_codes = message.get("stocks", [])
            print(f"Received request for stocks: {stock_codes}")

            data = await fetch_stock_data(stock_codes, url_template, max_concurrency=10)  # 异步爬取所有股票数据，并发限制为10
            await socket.send_json(data)  # 异步返回数据
        except Exception as e:
            logger.error(f"Error: {e}")
            socket.send_json({})  # 返回空数据，避免客户端卡住

if __name__ == "__main__":
    asyncio.run(main())