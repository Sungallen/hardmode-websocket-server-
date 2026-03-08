from __future__ import annotations

"""
Abstraction over MQTT (primary) and HTTP POST (fallback).

Both the core loop and all agents use this. If an MQTT broker is
available, messages go through pub/sub. Otherwise, the core loop
POSTs directly to each agent's HTTP endpoint.
"""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class CommandBus:
    def __init__(
        self,
        mqtt_broker: str | None = None,
        mqtt_port: int = 1883,
        http_endpoints: list[str] | None = None,
    ):
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.http_endpoints = http_endpoints or []
        self._mqtt_client = None
        self._mqtt_connected = False
        self._handlers: dict[str, list] = {}  # topic_pattern -> [handler_fn, ...]
        self._http_server = None

    def connect(self) -> bool:
        """Try to connect to the MQTT broker. Returns True on success."""
        if not MQTT_AVAILABLE or not self.mqtt_broker:
            return False

        try:
            self._mqtt_client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2
            )
            self._mqtt_client.on_connect = self._on_connect
            self._mqtt_client.on_message = self._on_message
            self._mqtt_client.connect(self.mqtt_broker, self.mqtt_port, keepalive=60)
            self._mqtt_client.loop_start()
            # Give it a moment to connect
            import time
            time.sleep(0.5)
            return self._mqtt_connected
        except Exception as e:
            print(f"[CommandBus] MQTT connection failed: {e}")
            self._mqtt_client = None
            return False

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self._mqtt_connected = True
            # Re-subscribe on reconnect
            for pattern in self._handlers:
                client.subscribe(pattern)
        else:
            print(f"[CommandBus] MQTT connect failed with code {rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        for pattern, handlers in self._handlers.items():
            if self._topic_matches(pattern, msg.topic):
                for handler in handlers:
                    try:
                        handler(msg.topic, payload)
                    except Exception as e:
                        print(f"[CommandBus] Handler error on {msg.topic}: {e}")

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        """Simple MQTT wildcard matching for + and #."""
        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")

        for i, p in enumerate(pattern_parts):
            if p == "#":
                return True
            if i >= len(topic_parts):
                return False
            if p != "+" and p != topic_parts[i]:
                return False

        return len(pattern_parts) == len(topic_parts)

    def publish(self, topic: str, payload: dict):
        """Publish a message. Uses MQTT if connected, else HTTP POST fallback."""
        message = json.dumps(payload)

        if self._mqtt_connected and self._mqtt_client:
            self._mqtt_client.publish(topic, message)
            return

        # HTTP fallback
        if not REQUESTS_AVAILABLE:
            print(f"[CommandBus] No transport available for {topic}")
            return

        for endpoint in self.http_endpoints:
            try:
                requests.post(
                    f"{endpoint}/command",
                    data=message,
                    headers={
                        "Content-Type": "application/json",
                        "X-Reachy-Topic": topic,
                    },
                    timeout=5,
                )
            except Exception as e:
                print(f"[CommandBus] HTTP POST to {endpoint} failed: {e}")

    def subscribe(self, topic_pattern: str, handler):
        """Register a callback for messages matching the topic pattern."""
        if topic_pattern not in self._handlers:
            self._handlers[topic_pattern] = []
        self._handlers[topic_pattern].append(handler)

        if self._mqtt_connected and self._mqtt_client:
            self._mqtt_client.subscribe(topic_pattern)

    def loop_forever(self):
        """Block and process messages. For sub-agents."""
        if self._mqtt_connected and self._mqtt_client:
            self._mqtt_client.loop_forever()
        elif self._http_server:
            self._http_server.serve_forever()
        else:
            # No transport — just block
            import time
            while True:
                time.sleep(1)

    def start_http_server(self, port: int):
        """Start an HTTP fallback server for receiving commands."""
        bus = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/command":
                    self.send_response(404)
                    self.end_headers()
                    return

                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                topic = self.headers.get("X-Reachy-Topic", "unknown")

                try:
                    payload = json.loads(body)
                    for pattern, handlers in bus._handlers.items():
                        if bus._topic_matches(pattern, topic):
                            for handler in handlers:
                                handler(topic, payload)
                except Exception as e:
                    print(f"[HTTP] Error processing command: {e}")

                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def log_message(self, format, *args):
                pass  # Suppress request logs

        self._http_server = HTTPServer(("0.0.0.0", port), Handler)
        thread = threading.Thread(
            target=self._http_server.serve_forever, daemon=True
        )
        thread.start()
        print(f"[CommandBus] HTTP fallback server on port {port}")

    def disconnect(self):
        """Clean shutdown."""
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()
        if self._http_server:
            self._http_server.shutdown()
