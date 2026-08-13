from __future__ import annotations

import json
import mimetypes
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import copyfileobj
from urllib.parse import quote


HOST = "0.0.0.0"
PORT = 65331
ALIAS = "6"
MEDIA_PATH = Path(r"C:\Users\hipes\OneDrive\Desktop\Work\TV\Sources\SiteService Short.gif")
MEDIA_ROUTE = f"/media/{quote(MEDIA_PATH.name)}"
LOG_PATH = Path(r"C:\Users\hipes\OneDrive\Desktop\Work\TV\tv6_smoketest_hits.log")


def append_log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(message + "\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        message = "%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args)
        print(message, flush=True)
        append_log(message)

    def do_GET(self) -> None:
        try:
            if self.path == f"/receiver-state-alias/{ALIAS}":
                body = json.dumps(
                    {
                        "source_name": MEDIA_PATH.name,
                        "note": "TV 6 smoke test",
                        "mime_type": "image/gif",
                        "media_url": f"http://10.171.64.144:{PORT}{MEDIA_ROUTE}",
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == MEDIA_ROUTE and MEDIA_PATH.exists():
                mime_type = mimetypes.guess_type(MEDIA_PATH.name)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(MEDIA_PATH.stat().st_size))
                self.end_headers()
                with MEDIA_PATH.open("rb") as media_file:
                    copyfileobj(media_file, self.wfile)
                return

            self.send_response(404)
            self.end_headers()
        except Exception:
            stack = traceback.format_exc()
            print(stack, flush=True)
            append_log(stack.rstrip())
            raise


if __name__ == "__main__":
    LOG_PATH.write_text("", encoding="utf-8")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    startup = f"Serving TV 6 smoke test on http://10.171.64.144:{PORT}"
    print(startup, flush=True)
    append_log(startup)
    server.serve_forever()
