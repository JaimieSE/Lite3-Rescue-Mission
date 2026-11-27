"""ROS transport adapter. All outputs are advisory; no locomotion API is imported."""
import json
import uuid
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from .store import Store


class MissionNode(Node):
    def __init__(self):
        super().__init__('rescue_mission_store')
        self.declare_parameter('db_path', '~/.local/share/rescue_mission/practice.sqlite3')
        self.declare_parameter('objectives_path', '')
        self.declare_parameter('publish_requests', False)
        self.store = Store(self.get_parameter('db_path').value)
        objectives = self.get_parameter('objectives_path').value
        if objectives:
            from pathlib import Path
            self.store.seed(json.loads(Path(objectives).read_text(encoding='utf-8')))
        self.status_pub = self.create_publisher(String, '/mission/status', 10)
        self.request_pub = self.create_publisher(String, '/mission/requests', 10)
        self.error_pub = self.create_publisher(String, '/mission/errors', 10)
        self.create_subscription(String, '/mission/events', self.on_event, 10)
        self.create_timer(1.0, self.tick)
        self.get_logger().info('Mission memory ready. No robot motion commands are produced.')

    def on_event(self, msg):
        try:
            self.store.apply(json.loads(msg.data))
        except (ValueError, TypeError, KeyError) as exc:
            self.get_logger().warning('Rejected event: ' + str(exc))
            self.error_pub.publish(String(data=json.dumps({'error': str(exc)})))

    def tick(self):
        self.status_pub.publish(String(data=json.dumps(self.store.snapshot())))
        if self.get_parameter('publish_requests').value:
            # Republish the same pending ID until acknowledged. Consumers must deduplicate.
            request = self.store.issue(str(uuid.uuid4()))
            self.request_pub.publish(String(data=json.dumps(request)))

    def destroy_node(self):
        self.store.close()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MissionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
