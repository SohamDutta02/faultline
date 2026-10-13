import argparse
import os

import uvicorn

from .app import create_app


def main():
    ap = argparse.ArgumentParser(description="serve the faultline web app (API, UI, toolserver and proxy)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")), help="default $PORT or 8000")
    ap.add_argument("--db", default=None, help="sqlite path (default faultline.db)")
    args = ap.parse_args()

    print(f"faultline on http://{args.host}:{args.port}")
    uvicorn.run(create_app(db_path=args.db), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
