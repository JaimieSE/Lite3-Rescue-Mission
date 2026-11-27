"""Capture front-camera evidence and send decoded QR clues to mission memory."""

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String
from std_srvs.srv import Trigger


class FrontCamera(Node):
    def __init__(self):
        super().__init__('mission_front_camera')
        topic = self.declare_parameter('image_topic', '/builtin_camera/image_raw').value
        directory = self.declare_parameter(
            'evidence_dir', '~/.local/share/rescue_mission/front_camera'
        ).value
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.latest = None
        self.last_processed = None
        self.published_clues = set()
        self.decoder = cv2.QRCodeDetector()
        self.events = self.create_publisher(String, '/mission/events', 10)
        self.create_subscription(Image, topic, self.receive_image, qos_profile_sensor_data)
        self.create_service(Trigger, '/mission/capture_front_image', self.capture)
        self.create_timer(0.5, self.scan)

    def receive_image(self, message):
        self.latest = message

    def frame(self):
        message = self.latest
        if message is None:
            raise ValueError('Waiting for a front-camera image')
        stamp = message.header.stamp
        seconds = stamp.sec + stamp.nanosec / 1_000_000_000
        now = self.get_clock().now().nanoseconds / 1_000_000_000
        if not 0 <= now - seconds <= 3:
            raise ValueError('Waiting for a fresh front-camera image')
        if message.encoding != 'bgr8':
            raise ValueError(f'Expected bgr8, received {message.encoding}')
        rows = np.frombuffer(message.data, dtype=np.uint8).reshape(message.height, message.step)
        pixels = rows[:, :message.width * 3].reshape(message.height, message.width, 3)
        return pixels, datetime.fromtimestamp(seconds, timezone.utc).isoformat()

    def save(self, pixels, capture_id):
        path = self.directory / f'{capture_id}.jpg'
        if not cv2.imwrite(str(path), pixels):
            raise OSError(f'Could not save {path}')
        return str(path)

    def capture(self, request, response):
        try:
            pixels, _ = self.frame()
            response.message = self.save(pixels, uuid.uuid4().hex)
            response.success = True
        except (ValueError, OSError, cv2.error) as exc:
            response.message = str(exc)
        return response

    def scan(self):
        if self.latest is self.last_processed or not self.events.get_subscription_count():
            return
        try:
            pixels, observed_at = self.frame()
            found, codes, _, _ = self.decoder.detectAndDecodeMulti(pixels)
            if found:
                for code in dict.fromkeys(codes):
                    if code and code not in self.published_clues:
                        self.publish_clue(code, pixels, observed_at)
        except (ValueError, OSError, cv2.error) as exc:
            self.get_logger().warning(str(exc), throttle_duration_sec=5)
        finally:
            self.last_processed = self.latest

    def publish_clue(self, code, pixels, observed_at):
        clue_id = uuid.uuid4().hex
        evidence = self.save(pixels, clue_id)
        self.events.publish(String(data=json.dumps({
            'event_id': clue_id,
            'type': 'qr',
            'source': 'front_camera',
            'observed_at': observed_at,
            'data': {'id': clue_id, 'text': code, 'evidence_ref': evidence},
        })))
        self.published_clues.add(code)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = FrontCamera()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()
