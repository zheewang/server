import mysql.connector
from flask import Blueprint, jsonify
import mysql.connector
from app_init import config, cache

db_config = config['database']
if db_config is None:
    raise ValueError('No database configuration found')

sectors_bp = Blueprint('sectors', __name__)
   
# API 端点：获取所有板块信息
@sectors_bp.route('/sectors', methods=['GET'])
@cache.cached(timeout=300, query_string=True)
def get_sectors():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT SectorIndexCode, SectorIndexName, THSSectorIndex FROM sector_index_info")
        sectors = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(sectors)
    except mysql.connector.Error as err:
        return jsonify({'error': str(err)}), 500
    

