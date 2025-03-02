# blueprints/limitup_unfilled_orders.py
from flask import Blueprint, jsonify, request
from app_init import app, db, cache, socketio, LimitUpUnfilledOrdersStocks, StockPopularityRanking, StockTurnoverRanking, DailyLimitUpStocks, DailyStockData, TradingDay, LimitUpStreakStocks
from blueprints.common import get_nearest_trading_date, get_recent_trading_dates, merge_stock_data
from blueprints.stock_pool_manager import update_stocks_pool, get_realtime_data
from datetime import datetime, timedelta
import pytz
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
enable_logging_again()

limitup_unfilled_orders_bp = Blueprint('limitup_unfilled_orders', __name__)

limitup_stock_codes = set()
limitup_stock_codes_lock = gevent.lock.Semaphore()

def get_beijing_time():
    return datetime.now(pytz.timezone('Asia/Shanghai'))

def get_intervals():
    now_utc = datetime.utcnow()
    beijing_tz = pytz.timezone('Asia/Shanghai')
    now_beijing = now_utc.replace(tzinfo=pytz.utc).astimezone(beijing_tz)

    time_9_00 = now_beijing.replace(hour=9, minute=0, second=0, microsecond=0)
    time_9_10 = now_beijing.replace(hour=9, minute=10, second=0, microsecond=0)
    time_11_30 = now_beijing.replace(hour=11, minute=30, second=0, microsecond=0)
    time_12_50 = now_beijing.replace(hour=12, minute=50, second=0, microsecond=0)
    time_13_00 = now_beijing.replace(hour=13, minute=0, second=0, microsecond=0)
    time_15_00 = now_beijing.replace(hour=15, minute=0, second=0, microsecond=0)

    if now_beijing < time_9_10:
        if now_beijing < time_9_00:
            time_diff = time_9_00 - now_beijing
            return time_diff.total_seconds()
        else:
            return 30
    elif time_9_10 <= now_beijing < time_11_30:
        return 30
    elif time_11_30 <= now_beijing < time_13_00:
        if now_beijing < time_12_50:
            time_diff = time_12_50 - now_beijing
            return time_diff.total_seconds()
        else:
            return 30
    elif time_13_00 <= now_beijing < time_15_00:
        return 30
    else:
        return 3600

def get_latest_limitup_stocks():
    with limitup_stock_codes_lock:
        local_stock_codes = list(limitup_stock_codes).copy()   

    app.logger.debug(f"get_latest_limitup_stocks:{local_stock_codes}")  

    return local_stock_codes 


@limitup_unfilled_orders_bp.route('/limitup_unfilled_orders_data', methods=['GET'])
@cache.cached(timeout=300, query_string=True)
def get_limitup_unfilled_orders_data():
    date_str = request.args.get('date')
    
    if not date_str:
        app.logger.error("Date parameter is missing")
        return jsonify({'error': 'Date parameter is required'}), 400

   
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        app.logger.debug(f"Processing request for date: {date_str}")
        
        with app.app_context():
            nearest_trading_date = get_nearest_trading_date(target_date, TradingDay)
            app.logger.debug(f"Nearest trading date: {nearest_trading_date}")
            if not nearest_trading_date:
                app.logger.info(f"No trading day found before the specified date: {date_str}")

            recent_trading_dates = get_recent_trading_dates(target_date, 5, TradingDay) if nearest_trading_date else []
            app.logger.debug(f"Recent trading dates: {recent_trading_dates}")

            limitup_data = db.session.query(LimitUpUnfilledOrdersStocks).filter_by(LimitUpDate=target_date).all()
            app.logger.debug(f"Limit up data count: {len(limitup_data)}")
            if not limitup_data:
                app.logger.info(f"No limit up unfilled orders data found for date: {date_str}")
                return jsonify([]), 200
                
            stock_codes = [l.StockCode for l in limitup_data]
            app.logger.debug(f" limitup_data Stock codes: {stock_codes}")

            streak_data = db.session.query(LimitUpStreakStocks).filter(
                LimitUpStreakStocks.LimitUpDate == target_date,
                LimitUpStreakStocks.StockCode.in_(stock_codes)
            ).all()
            streak_dict = {s.StockCode: s.StreakDays for s in streak_data}
            app.logger.debug(f"Streak data: {streak_dict}")

            with limitup_stock_codes_lock:
                limitup_stock_codes.update(stock_codes)
                app.logger.debug(f"Updated limitup_stock_codes: {limitup_stock_codes}")

            update_stocks_pool(limitup_stock_codes, caller='limitup_unfilled_orders')

            pop_dict, turnover_dict, limitup_dict, daily_dict = merge_stock_data(
                None, stock_codes, nearest_trading_date, recent_trading_dates,
                (StockPopularityRanking, StockTurnoverRanking, DailyLimitUpStocks, DailyStockData)
            ) if nearest_trading_date else ({}, {}, {}, {})
            app.logger.debug(f"Merged data - Pop: {len(pop_dict)}, Turnover: {len(turnover_dict)}, LimitUp: {len(limitup_dict)}, Daily: {len(daily_dict)}")

            realtime_data_copy = get_realtime_data()  #get_realtime_data('limitup_realtime')
            app.logger.debug(f"Realtime data: {realtime_data_copy}")

            stock_data = []

            for limitup in limitup_data:
                stock_info = {
                    'StockCode': limitup.StockCode,
                    'StockName': limitup.StockName,
                    'StreakDays': streak_dict.get(limitup.StockCode, None),
                    'OpeningAmount': float(limitup.OpeningAmount) if limitup.OpeningAmount is not None else None,
                    'LimitUpOrderAmount': float(limitup.LimitUpOrderAmount) if limitup.LimitUpOrderAmount is not None else None,
                    'FirstLimitUpTime': limitup.FirstLimitUpTime,
                    'FinalLimitUpTime': limitup.FinalLimitUpTime,
                    'LimitUpOpenTimes': limitup.LimitUpOpenTimes,
                    'PopularityRank': None,
                    'TurnoverAmount': None,
                    'TurnoverRank': None,
                    'ReasonCategory': None,
                    'RealtimeChange': None,
                    'RealtimePrice': None,
                    'recent_data': []
                }

                if limitup.StockCode in pop_dict:
                    p = pop_dict[limitup.StockCode]
                    stock_info['PopularityRank'] = p.PopularityRank

                if limitup.StockCode in turnover_dict:
                    t = turnover_dict[limitup.StockCode]
                    stock_info['TurnoverAmount'] = t.TurnoverAmount
                    stock_info['TurnoverRank'] = t.TurnoverRank

                if limitup.StockCode in limitup_dict:
                    l = limitup_dict[limitup.StockCode]
                    stock_info['ReasonCategory'] = l.ReasonCategory

                if limitup.StockCode in realtime_data_copy:
                    r = realtime_data_copy[limitup.StockCode]
                    stock_info['RealtimeChange'] = r['RealtimeChange']
                    stock_info['RealtimePrice'] = r['RealtimePrice']

                if limitup.StockCode in daily_dict:
                    daily_records = sorted(
                        daily_dict[limitup.StockCode],
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

            app.logger.debug(f"Returning {len(stock_data)} records")

            return jsonify(stock_data)

    except ValueError:
        app.logger.error(f"Invalid date format: {date_str}")
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    except Exception as e:
        app.logger.error(f"Unexpected error: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

