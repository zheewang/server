# blueprints/custom_stock.py
from flask import Blueprint, jsonify, request
import gevent.lock
from app_init import app, db, cache, socketio, StockPopularityRanking, StockTurnoverRanking, DailyLimitUpStocks, DailyStockData, TradingDay
from blueprints.common import get_nearest_trading_date, get_recent_trading_dates, merge_stock_data
from datetime import datetime, timedelta
import pytz
import os
import logging
import gevent


logger = logging.getLogger(__name__)

# 假设你已经全局配置了 logging
def disable_logging_temporarily():
    logging.disable(logging.CRITICAL)  # 禁用所有级别低于 CRITICAL 的日志

def enable_logging_again():
    logging.disable(logging.NOTSET)  # 重新启用所有日志

# 在你的特定页面或模块中调用 disable_logging_temporarily()
# 在需要重新启用日志时调用 enable_logging_again()
disable_logging_temporarily()


custom_stock_bp = Blueprint('custom_stock', __name__)

# 全局变量存储股票代码
stock_codes = []
stock_codes_lock = gevent.lock.Semaphore()

def get_beijing_time():
    return datetime.now(pytz.timezone('Asia/Shanghai'))

def read_stock_codes(file_path='stocks.txt'):
    global stock_codes
    try:
        if not os.path.exists(file_path):
            logger.warning(f"Stock file {file_path} not found, returning empty list")
            with stock_codes_lock:
                stock_codes = []
            return stock_codes
        with open(file_path, 'r') as f:
            with stock_codes_lock:
                stock_codes = [line.strip() for line in f if line.strip()]
        logger.debug(f"Read {len(stock_codes)} stock codes from {file_path}")
        return stock_codes
    except Exception as e:
        logger.error(f"Error reading stock codes from {file_path}: {str(e)}")
        with stock_codes_lock:
            stock_codes = []
        return stock_codes

def write_stock_codes(stock_codes_to_write, file_path='stocks.txt'):
    global stock_codes
    try:
        with open(file_path, 'w') as f:
            for code in stock_codes_to_write:
                f.write(f"{code}\n")
        with stock_codes_lock:
            stock_codes = stock_codes_to_write[:]

        logger.debug(f"Successfully wrote {len(stock_codes)} stock codes to {file_path}")

        return True
    except Exception as e:
        logger.error(f"Error writing stock codes to {file_path}: {str(e)}")
        return False

@custom_stock_bp.route('/custom_stock_data', methods=['GET'])
@cache.cached(timeout=300, query_string=True)
def get_custom_stock_data():
    from blueprints.stock_pool_manager import update_stocks_pool, get_realtime_data #延迟导入
    global stock_codes
    new_stock_code = request.args.get('new_stock_code')
    target_date = get_beijing_time().date()
    
    try:
        beijing_time = get_beijing_time()
        is_trading_day = db.session.query(TradingDay).filter_by(trading_date=target_date).first() is not None

        if not is_trading_day:
            display_date = get_nearest_trading_date(target_date, TradingDay)
            yesterday = display_date
        elif is_trading_day and beijing_time.hour < 16:
            display_date = get_nearest_trading_date(target_date - timedelta(days=1), TradingDay)
            yesterday = display_date
        elif is_trading_day and beijing_time.hour >= 18:
            display_date = target_date
            yesterday = get_nearest_trading_date(target_date - timedelta(days=1), TradingDay)
        else:
            display_date = get_nearest_trading_date(target_date - timedelta(days=1), TradingDay)
            yesterday = display_date

        if not display_date:
            logger.error("No trading day found for display_date")
            return jsonify({'error': 'No trading day found for display_date'}), 404

        if not yesterday:
            logger.warning("No previous trading day found, yesterday data will be unavailable")

        local_stock_codes = read_stock_codes()
        if new_stock_code and new_stock_code not in local_stock_codes:
            local_stock_codes.append(new_stock_code)
            write_stock_codes(local_stock_codes)

        if not local_stock_codes:
            logger.error("No stock codes provided or found in file")
            return jsonify({'error': 'No stock codes provided'}), 400

        update_stocks_pool(local_stock_codes, caller='custom_stock')

        with app.app_context():
            recent_trading_dates = get_recent_trading_dates(target_date, 5, TradingDay)
            if not recent_trading_dates:
                logger.error("No recent trading dates available")
                return jsonify({'error': 'No recent trading dates available'}), 404

            pop_dict, turnover_dict, limitup_dict, daily_dict = merge_stock_data(
                None, local_stock_codes, display_date, recent_trading_dates,
                (StockPopularityRanking, StockTurnoverRanking, DailyLimitUpStocks, DailyStockData)
            )

            yesterday_data = db.session.query(DailyStockData).filter(
                DailyStockData.StockCode.in_(local_stock_codes),
                DailyStockData.trading_Date == yesterday
            ).all() if yesterday else []
            yesterday_dict = {d.StockCode: d for d in yesterday_data}

            stock_data = []
            realtime_data_copy = get_realtime_data() #get_realtime_data('realtime')
            
            for code in local_stock_codes:
                yesterday_record = yesterday_dict.get(code)
                limitup_record = limitup_dict.get(code)
                realtime = realtime_data_copy.get(code, {'RealtimePrice': None, 'RealtimeChange': None})
                
                stock_info = {
                    'StockCode': code,
                    'StockName': getattr(pop_dict.get(code), 'StockName', 'Unknown'),
                    'PopularityRank': getattr(pop_dict.get(code), 'PopularityRank', None),
                    'TurnoverAmount': getattr(turnover_dict.get(code), 'TurnoverAmount', None),
                    'TurnoverRank': getattr(turnover_dict.get(code), 'TurnoverRank', None),
                    'LatestLimitUpDate': limitup_record.LatestLimitUpDate.strftime('%Y-%m-%d') if limitup_record and limitup_record.LatestLimitUpDate else None,
                    'ReasonCategory': limitup_record.ReasonCategory if limitup_record else None,
                    'RealtimeChange': realtime['RealtimeChange'],
                    'RealtimePrice': realtime['RealtimePrice'],
                    'YesterdayChange': float(yesterday_record.change_percent) if yesterday_record and yesterday_record.change_percent is not None else None,
                    'YesterdayClose': float(yesterday_record.close) if yesterday_record and yesterday_record.close is not None else None,
                    'recent_data': []
                }

                if code in daily_dict:
                    daily_records = sorted(
                        daily_dict[code],
                        key=lambda x: x.trading_Date,
                        reverse=True
                    )
                    for d in daily_records[:5]:
                        day_data = {
                            'trading_Date': d.trading_Date.strftime('%Y-%m-%d'),
                            'change_percent': float(d.change_percent) if d.change_percent is not None else 0,
                            'close': float(d.close) if d.close is not None else None,
                            'high': float(d.high) if d.high is not None else None,
                            'open': float(d.open) if d.open is not None else None,
                            'low': float(d.low) if d.low is not None else None
                        }
                        stock_info['recent_data'].append(day_data)

                stock_data.append(stock_info)

            logger.debug(f"Returning {len(stock_data)} records")
            return jsonify(stock_data)

    except Exception as e:
        logger.error(f"Unexpected error for stock codes {stock_codes}: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@custom_stock_bp.route('/save_stock_codes', methods=['POST'])
def save_stock_codes():
    try:
        stock_codes_new = request.json.get('stock_codes', [])
        if not stock_codes_new:
            return jsonify({'error': 'No stock codes provided'}), 400

        success = write_stock_codes(stock_codes_new)
        if success:
            return jsonify({'message': 'Stock codes saved successfully'}), 200
        else:
            return jsonify({'error': 'Failed to save stock codes'}), 500
    except Exception as e:
        logger.error(f"Error saving stock codes: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

