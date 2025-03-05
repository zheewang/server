import zmq
import time

class RealtimeUpdater:
    def __init__(self):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)  # REQ (Request) 模式
        self.socket.connect("tcp://127.0.0.1:5555")  # 连接 Selenium 服务器

    def get_realtime_data(self, stock_codes):
        """向 Selenium 进程请求实时股票数据"""
        try:
            request = {"stocks": stock_codes}
            self.socket.send_json(request)  # 发送请求
            if self.socket.poll(10000):  # 等待最多 10 秒
                updated_data = self.socket.recv_json()  # 接收返回数据
                return updated_data
            else:
                print("[Error] Timeout waiting for Selenium response")
                return {}
        except Exception as e:
            print(f"[Error] ZMQ communication failed: {e}")
            return {}

# 使用示例
if __name__ == "__main__":
    updater = RealtimeUpdater()
    stocks = ["600519", "000158",'600602']  # 需要查询的股票代码
    result = updater.get_realtime_data(stocks)
    print("Received Realtime Data:", result)
