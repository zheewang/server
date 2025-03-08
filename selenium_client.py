import zmq
import time

class RealtimeUpdater:
    def __init__(self):
        self.context = zmq.Context()
        # 用于发送请求的 PUB socket
        self.pub_socket = self.context.socket(zmq.PUB)
        self.pub_socket.connect("tcp://127.0.0.1:5556")
        # 用于接收数据的 SUB socket
        self.sub_socket = self.context.socket(zmq.SUB)
        self.sub_socket.connect("tcp://127.0.0.1:5555")
        self.sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")  # 订阅所有消息

    def get_realtime_data(self, stock_codes):
        """向 Selenium 服务器请求实时股票数据并接收分片结果"""
        updated_data = {}
        try:
            # 发送请求
            request = {"stocks": stock_codes}
            self.pub_socket.send_json(request)
            print(f"Sent request for stocks: {stock_codes}")
            time.sleep(1)  # 短暂延迟，确保订阅生效

            # 接收分片数据
            done = False
            while not done:
                if self.sub_socket.poll(10000):  # 等待最多 10 秒
                    data = self.sub_socket.recv_json()
                    if "done" in data and data["done"]:
                        print("Received completion signal")
                        done = True
                    else:
                        print(f"Received batch data: {data}")
                        updated_data.update(data)
                else:
                    print("[Error] Timeout waiting for Selenium response")
                    break
        except Exception as e:
            print(f"[Error] ZMQ communication failed: {e}")
        finally:
            return updated_data

    def close(self):
        """清理资源"""
        self.sub_socket.close()
        self.pub_socket.close()
        self.context.term()

# 使用示例
if __name__ == "__main__":
    updater = RealtimeUpdater()
    stocks = ["600519", "000158", "600602"]  # 需要查询的股票代码
    result = updater.get_realtime_data(stocks)
    print("Received Realtime Data:", result)
    updater.close()