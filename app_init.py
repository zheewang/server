# app_init.py
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
from flask_socketio import SocketIO
from sqlalchemy import inspect
import yaml
import sqlalchemy
import logging


# 配置全局日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='C:\\WebApp\\server\\app.log',
    filemode='a'
)

app = Flask(__name__, static_url_path='/static')
app.secret_key = '4ba25e1cfeb150ae1e979fee3928aeb8'  # 设置 Session 密钥

# 加载 config.yaml
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# 配置数据库
app.config['SQLALCHEMY_DATABASE_URI'] = (
    f"mysql+pymysql://{config['database']['user']}:{config['database']['password']}"
    f"@{config['database']['host']}:{config['database']['port']}/{config['database']['db']}"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['TUSHARE_TOKEN'] = config['tushare']['token']
app.config['HOST'] = config['server']['host']  # 添加 HOST
app.config['PORT'] = config['server']['port']  # 添加 PORT

db = SQLAlchemy(app)
cache = Cache(app, config={'CACHE_TYPE': 'simple'})
socketio = SocketIO(app, async_mode='gevent', cors_allowed_origins="*")

logger = app.logger
def init_socketio(socketio):
    @socketio.on('connect', namespace='/stocks_realtime')
    def handle_connect():
        logger.debug("Client connected to /stocks_realtime namespace")

    @socketio.on('disconnect', namespace='/stocks_realtime')
    def handle_disconnect():
        logger.debug("Client disconnected from /stocks_realtime namespace")

init_socketio(socketio)  # 初始化SocketIO,绑定到特点的事件命名空间


# 动态生成数据模型的基类
def generate_model(table_name):
    with app.app_context():
        inspector = inspect(db.engine)
        try:
            columns = inspector.get_columns(table_name)
        except Exception as e:
            print(f"Error getting columns for {table_name}: {str(e)}")
            return None

        pk_constraint = inspector.get_pk_constraint(table_name)
        primary_keys = pk_constraint['constrained_columns']

        column_definitions = {}

        for column in columns:
            column_name = column['name']
            column_type = column['type']

            if isinstance(column_type, sqlalchemy.Integer):
                column_type = db.Integer
            elif isinstance(column_type, sqlalchemy.String):
                column_type = db.String
            elif isinstance(column_type, sqlalchemy.DateTime):
                column_type = db.DateTime
            elif isinstance(column_type, sqlalchemy.types.Boolean):
                column_type = db.Boolean
            elif isinstance(column_type, sqlalchemy.types.Float):
                column_type = db.Float
            elif isinstance(column_type, sqlalchemy.types.Numeric):
                column_type = db.Numeric
            elif isinstance(column_type, sqlalchemy.types.Text):
                column_type = db.Text
            elif isinstance(column_type, sqlalchemy.types.Date):
                column_type = db.Date
            elif isinstance(column_type, sqlalchemy.types.Time):
                column_type = db.Time
            elif isinstance(column_type, sqlalchemy.types.LargeBinary):
                column_type = db.LargeBinary
            else:
                column_type = db.String

            if column_name in primary_keys:
                column_definitions[column_name] = db.Column(column_type, primary_key=True)
            else:
                column_definitions[column_name] = db.Column(column_type)

        return type(table_name, (db.Model,), column_definitions)

# 生成所有需要的模型
StockPopularityRanking = generate_model('stock_popularity_ranking')
StockTurnoverRanking = generate_model('stock_turnover_ranking')
DailyLimitUpStocks = generate_model('daily_limitup_stocks')
DailyStockData = generate_model('daily_stock_data')
TradingDay = generate_model('trading_day')
MaStrategies = generate_model('ma_strategies')
StockSectorMapping = generate_model('stock_sector_mapping')  # 添加缺失的模型
LimitUpUnfilledOrdersStocks = generate_model('limitup_unfilled_orders')
LimitUpStreakStocks = generate_model('limitup_streak_stocks')  # 新增模型