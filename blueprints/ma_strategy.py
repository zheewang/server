from flask import Blueprint, jsonify, request
from app_init import app, db, cache, socketio,MaStrategies, StockPopularityRanking, StockTurnoverRanking, DailyLimitUpStocks, DailyStockData, TradingDay
from blueprints.common import get_nearest_trading_date, get_recent_trading_dates, merge_stock_data
from datetime import datetime
import logging
from blueprints.stock_pool_manager import  update_stocks_pool,get_realtime_data
import gevent
from flask import session

logger = logging.getLogger(__name__)

ma_strategy_bp = Blueprint('ma_strategy', __name__)

ma_strategy_stock_codes = set()
ma_strategy_stock_codes_lock = gevent.lock.Semaphore()

def get_latest_ma_strategy_stocks():
    with ma_strategy_stock_codes_lock:
        local_stock_codes = list(ma_strategy_stock_codes).copy()    

    logger.debug(f"get_latest_ma_strategy_stocks: {local_stock_codes}")   
    return local_stock_codes 

@ma_strategy_bp.route('/ma_strategy_data', methods=['GET'])
def get_ma_strategy_data():
    date_str = request.args.get('date')
    
    if not date_str:
        return jsonify({'error': 'Date parameter is required'}), 400

    cache_key = f"ma_strategy_data_{date_str}"
    if cache_key in session:
        logger.debug(f"Returning cached data for {date_str}")
        return jsonify(session[cache_key])

    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        logger.debug(f"Processing request for date: {date_str}")
        
        with app.app_context():
            nearest_trading_date = get_nearest_trading_date(target_date, TradingDay)
            if not nearest_trading_date:
                return jsonify({'error': 'No trading day found before the specified date'}), 404
            
            recent_trading_dates = get_recent_trading_dates(target_date, 5, TradingDay)
            if not recent_trading_dates:
                return jsonify({'error': 'No recent trading dates available'}), 404

            ma_strategy_data = db.session.query(MaStrategies).filter_by(trading_Date=target_date).all()
            if not ma_strategy_data:
                logger.info(f"No MA strategy data found for date: {date_str}")
                return jsonify({'message': 'No MA strategy data found for the specified date'}), 200
                
            stock_codes = [m.StockCode for m in ma_strategy_data]
            logger.debug(f" ma_strategy Stock codes from database: {stock_codes}")

            # 更新本地股票列表并加入队列
            with ma_strategy_stock_codes_lock:
                ma_strategy_stock_codes.update(stock_codes)
                logger.debug(f"Updated ma_strategy_stock_codes: {ma_strategy_stock_codes}")

            update_stocks_pool(ma_strategy_stock_codes, caller='ma_strategy')  # 加入队列

            pop_dict, turnover_dict, limitup_dict, daily_dict = merge_stock_data(
                ma_strategy_data, stock_codes, nearest_trading_date, recent_trading_dates,
                (StockPopularityRanking, StockTurnoverRanking, DailyLimitUpStocks, DailyStockData)
            )

            realtime_data_copy = get_realtime_data() #get_realtime_data('realtime')

            stock_data = []

            for ma in ma_strategy_data:

                realtime = realtime_data_copy.get(ma.StockCode, {'RealtimePrice': None, 'RealtimeChange': None})

                stock_info = {
                    'StockCode': ma.StockCode,
                    'StockName': ma.StockName,
                    'trading_Date': ma.trading_Date.strftime('%Y-%m-%d'),
                    'type': ma.type,
                    'PopularityRank': None,
                    'TurnoverAmount': None,
                    'TurnoverRank': None,
                    'LatestLimitUpDate': None,
                    'ReasonCategory': None,
                    'RealtimeChange': realtime['RealtimeChange'],
                    'RealtimePrice': realtime['RealtimePrice'],                    
                    'recent_data': []
                }

                if ma.StockCode in pop_dict:
                    p = pop_dict[ma.StockCode]
                    stock_info['PopularityRank'] = p.PopularityRank

                if ma.StockCode in turnover_dict:
                    t = turnover_dict[ma.StockCode]
                    stock_info['TurnoverAmount'] = t.TurnoverAmount
                    stock_info['TurnoverRank'] = t.TurnoverRank

                if ma.StockCode in limitup_dict:
                    l = limitup_dict[ma.StockCode]
                    stock_info['LatestLimitUpDate'] = l.LatestLimitUpDate.strftime('%Y-%m-%d') if l.LatestLimitUpDate else None
                    stock_info['ReasonCategory'] = l.ReasonCategory

                if ma.StockCode in daily_dict:
                    daily_records = sorted(
                        daily_dict[ma.StockCode],
                        key=lambda x: x.trading_Date,
                        reverse=True
                    )
                    for d in daily_records[:5]:
                        day_data = {
                            'trading_Date': d.trading_Date.strftime('%Y-%m-%d'),
                            'change_percent': float(d.change_percent) if d.change_percent else 0,
                            'close': float(d.close) if d.close else None,
                            'high': float(d.high) if d.high else None,
                            'open': float(d.open) if d.open else None,
                            'low': float(d.low) if d.low else None
                        }
                        stock_info['recent_data'].append(day_data)

                stock_data.append(stock_info)

            logger.debug(f"Returning {len(stock_data)} records")

            session[cache_key] = stock_data
            return jsonify(stock_data)

    except ValueError:
        logger.error(f"Invalid date format: {date_str}")
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500
    
@socketio.on('connect', namespace='/ma_strategy')
def handle_connect():
    print('Client connected to /ma_strategy namespace')
    logger.error("Client connected to /ma_strategy namespace")