# blueprints/common.py
from flask import Blueprint
from app_init import app, db
import logging

logger = logging.getLogger(__name__)

def get_nearest_trading_date(target_date, TradingDay):
    try:
        result = db.session.query(TradingDay.trading_date).filter(
            TradingDay.trading_date <= target_date
        ).order_by(TradingDay.trading_date.desc()).first()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Error in get_nearest_trading_date: {str(e)}")
        return None

def get_recent_trading_dates(target_date, days, TradingDay):
    try:
        results = db.session.query(TradingDay.trading_date).filter(
            TradingDay.trading_date <= target_date
        ).order_by(TradingDay.trading_date.desc()).limit(days).all()
        return [r[0].strftime('%Y-%m-%d') for r in results]
    except Exception as e:
        logger.error(f"Error in get_recent_trading_dates: {str(e)}")
        return []

def merge_stock_data(base_data, stock_codes, nearest_trading_date, recent_trading_dates, models):
    StockPopularityRanking, StockTurnoverRanking, DailyLimitUpStocks, DailyStockData = models
    
    pop_subquery = db.session.query(StockPopularityRanking).filter(
        StockPopularityRanking.StockCode.in_(stock_codes),
        StockPopularityRanking.date == nearest_trading_date
    ).subquery()
    popularity_data = db.session.query(pop_subquery).all()

    turnover_subquery = db.session.query(StockTurnoverRanking).filter(
        StockTurnoverRanking.StockCode.in_(stock_codes),
        StockTurnoverRanking.date == nearest_trading_date
    ).subquery()
    turnover_data = db.session.query(turnover_subquery).all()

    limitup_subquery = db.session.query(DailyLimitUpStocks).filter(
        DailyLimitUpStocks.StockCode.in_(stock_codes)
    ).subquery()
    limitup_data = db.session.query(limitup_subquery).all()

    daily_subquery = db.session.query(DailyStockData).filter(
        DailyStockData.StockCode.in_(stock_codes),
        DailyStockData.trading_Date.in_(recent_trading_dates)
    ).order_by(DailyStockData.trading_Date.desc()).subquery()
    daily_data = db.session.query(daily_subquery).all()

    pop_dict = {p.StockCode: p for p in popularity_data}
    turnover_dict = {t.StockCode: t for t in turnover_data}
    limitup_dict = {l.StockCode: l for l in limitup_data}
    daily_dict = {}
    for d in daily_data:
        if d.StockCode not in daily_dict:
            daily_dict[d.StockCode] = []
        daily_dict[d.StockCode].append(d)

    return pop_dict, turnover_dict, limitup_dict, daily_dict

