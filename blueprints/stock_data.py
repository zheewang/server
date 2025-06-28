from flask import Blueprint, jsonify, request
from app_init import app, db, cache, StockPopularityRanking, StockTurnoverRanking, DailyLimitUpStocks, StockSectorMapping, DailyStockData, TradingDay
from blueprints.common import get_nearest_trading_date, get_recent_trading_dates, merge_stock_data
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

stock_data_bp = Blueprint('stock_data', __name__)

@stock_data_bp.route('/stock_data', methods=['GET'])
@cache.cached(timeout=30, query_string=True)
def get_stock_data():
    # 修改 1：更改参数为 sector_codes
    date_str = request.args.get('date')
    sector_codes = request.args.get('sector_codes')
    
    if not date_str:
        return jsonify({'error': 'Date parameter is required'}), 400



    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        logger.debug(f"Processing request for date: {date_str}, sector_codes: {sector_codes}")
        
        with app.app_context():
            nearest_trading_date = get_nearest_trading_date(target_date, TradingDay)
            if not nearest_trading_date:
                return jsonify({'error': 'No trading day found before the specified date'}), 404
            
            recent_trading_dates = get_recent_trading_dates(target_date, 3, TradingDay)
            if not recent_trading_dates:
                return jsonify({'error': 'No recent trading dates available'}), 404

            query = db.session.query(StockPopularityRanking).filter_by(date=target_date)
            # 修改 2：支持多板块交集查询
            if sector_codes:
                sector_codes_list = sector_codes.split(',')
                if len(sector_codes_list) == 1:
                    query = query.join(
                        StockSectorMapping,
                        StockPopularityRanking.StockCode == StockSectorMapping.StockCode
                    ).filter(StockSectorMapping.SectorCode == sector_codes_list[0])
                else:
                    query = query.join(
                        StockSectorMapping,
                        StockPopularityRanking.StockCode == StockSectorMapping.StockCode
                    ).filter(
                        StockSectorMapping.SectorCode.in_(sector_codes_list)
                    ).group_by(StockPopularityRanking.StockCode).having(
                        db.func.count(db.distinct(StockSectorMapping.SectorCode)) == len(sector_codes_list)
                    )

            popularity_data = query.all()
            if not popularity_data:
                logger.info(f"No data found for date: {date_str}, sector_codes: {sector_codes}")
                return jsonify({'message': 'No data found for the specified criteria'}), 200
                
            stock_codes = [p.StockCode for p in popularity_data]

            pop_dict, turnover_dict, limitup_dict, daily_dict = merge_stock_data(
                popularity_data, stock_codes, nearest_trading_date, recent_trading_dates,
                (StockPopularityRanking, StockTurnoverRanking, DailyLimitUpStocks, DailyStockData)
            )

            # 修改 3：简化实时数据获取，模仿 limitup_unfilled_orders.py
            from blueprints.stock_pool_manager import global_updater
            realtime_data = global_updater.get_realtime_data(stock_codes, source='mairui', caller='stock_data')
            logger.debug(f"Realtime data") #: {realtime_data}


            stock_data = []
            for pop in popularity_data:
                stock_info = {
                    'StockCode': pop.StockCode,
                    'StockName': pop.StockName,
                    'PopularityRank': pop.PopularityRank,
                    'date': pop.date.strftime('%Y-%m-%d'),
                    'trading_date': nearest_trading_date.strftime('%Y-%m-%d'),
                    'TurnoverAmount': None,
                    'TurnoverRank': None,
                    'LatestLimitUpDate': None,
                    'ReasonCategory': None,
                    # 修改 4：添加实时数据字段
                    'RealtimeChange': 'N/A',
                    'RealtimePrice': 'N/A',
                    'recent_data': []
                }

                if pop.StockCode in turnover_dict:
                    t = turnover_dict[pop.StockCode]
                    stock_info['TurnoverAmount'] = t.TurnoverAmount
                    stock_info['TurnoverRank'] = t.TurnoverRank

                if pop.StockCode in limitup_dict:
                    l = limitup_dict[pop.StockCode]
                    stock_info['LatestLimitUpDate'] = l.LatestLimitUpDate.strftime('%Y-%m-%d') if l.LatestLimitUpDate else None
                    stock_info['ReasonCategory'] = l.ReasonCategory

                if pop.StockCode in daily_dict:
                    daily_records = sorted(
                        daily_dict[pop.StockCode],
                        key=lambda x: x.trading_Date,
                        reverse=True
                    )
                    for d in daily_records[:3]:
                        day_data = {
                            'trading_Date': d.trading_Date.strftime('%Y-%m-%d'),
                            'change_percent': float(d.change_percent) if d.change_percent else 0,
                            'close': float(d.close) if d.close else None,
                            'high': float(d.high) if d.high else None,
                            'open': float(d.open) if d.open else None,
                            'low': float(d.low) if d.low else None
                        }
                        stock_info['recent_data'].append(day_data)

                # 修改 5：简化实时数据填充，模仿 limitup_unfilled_orders.py
                if pop.StockCode in realtime_data:
                    r = realtime_data[pop.StockCode]
                    stock_info['RealtimeChange'] = r['RealtimeChange']
                    stock_info['RealtimePrice'] = r['RealtimePrice']

                stock_data.append(stock_info)

            logger.debug(f"Returning {len(stock_data)} records")
 
            return jsonify(stock_data)

    except ValueError:
        logger.error(f"Invalid date format: {date_str}")
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500