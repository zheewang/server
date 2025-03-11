# app.py

import gevent
from gevent import monkey
monkey.patch_all()

from flask_cors import CORS
from app_init import app,socketio
from blueprints.sectors import sectors_bp
from blueprints.stock_data import stock_data_bp
from blueprints.ma_strategy import ma_strategy_bp
from blueprints.custom_stock import custom_stock_bp
from flask import render_template
from blueprints.limitup_unfilled_orders import limitup_unfilled_orders_bp 
from blueprints.stock_pool_manager import global_updater

CORS(app)

app.register_blueprint(sectors_bp, url_prefix='/api')
app.register_blueprint(stock_data_bp, url_prefix='/api')
app.register_blueprint(ma_strategy_bp, url_prefix='/api')
app.register_blueprint(custom_stock_bp, url_prefix='/api')
app.register_blueprint(limitup_unfilled_orders_bp, url_prefix='/api')  # 确保注册

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stock_dashboard')
def stock_dashboard():
    return render_template('stock_dashboard.html')

@app.route('/ma_strategy_dashboard')
def ma_strategy_dashboard():
    return render_template('ma_strategy_dashboard.html')

@app.route('/custom_stock_dashboard')
def custom_stock_dashboard():
    return render_template('custom_stock_dashboard.html')

@app.route('/limitup_unfilled_orders_dashboard')  # 新增路由
def limitup_unfilled_orders_dashboard():
    return render_template('limitup_unfilled_orders_dashboard.html')


if __name__ == '__main__':
    with app.app_context():
        global_updater.sync_latest_stocks()  # 初始同步
        global_updater.start()  # 启动线程
    socketio.run(app, host=app.config['HOST'], port=app.config['PORT'], debug=False)
    print('Server started successfully')