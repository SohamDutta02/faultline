"""Run the toolserver and the proxy inside this process.

`python -m runner.stack` serves both until Ctrl-C (one command instead of two
terminals); `local_stack()` is what `sweep.run --serve` and the tests use.
"""

import argparse
import signal
import socket
import threading
import time
from contextlib import contextmanager

import uvicorn


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    def __init__(self, app, port: int):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self, timeout: float = 10.0):
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(f"server on port {self.server.config.port} failed to start")
            time.sleep(0.02)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


@contextmanager
def local_stack(toolserver_port: int | None = None, proxy_port: int | None = None):
    """Start both servers, wire the proxy to the toolserver, yield their URLs."""
    from proxy import app as proxy_app
    from toolserver.app import app as toolserver_app

    ts_port = toolserver_port or free_port()
    px_port = proxy_port or free_port()
    toolserver_url = f"http://127.0.0.1:{ts_port}"
    proxy_url = f"http://127.0.0.1:{px_port}"
    # the proxy and world are module-level singletons, so a nested stack must
    # hand the upstream back when it exits
    previous_upstream = str(proxy_app.state["client"].base_url)
    proxy_app.set_upstream(toolserver_url)

    servers = [_Server(toolserver_app, ts_port), _Server(proxy_app.app, px_port)]
    started = []
    try:
        for s in servers:
            s.start()
            started.append(s)
        yield toolserver_url, proxy_url
    finally:
        for s in reversed(started):
            s.stop()
        proxy_app.set_upstream(previous_upstream)


def main():
    ap = argparse.ArgumentParser(description="serve the toolserver and fault proxy")
    ap.add_argument("--toolserver-port", type=int, default=8100)
    ap.add_argument("--proxy-port", type=int, default=8200)
    args = ap.parse_args()

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    with local_stack(args.toolserver_port, args.proxy_port) as (ts, px):
        print(f"toolserver {ts}\nproxy      {px} -> {ts}\nctrl-c to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
