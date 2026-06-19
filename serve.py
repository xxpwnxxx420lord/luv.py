"""
Simple AI generated thing so i can check if games work <3
"""

import http.server
import socketserver
import webbrowser
import os

PORT = 8000


class CORSHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # These two headers are required for SharedArrayBuffer.
        # Without them the browser blocks SAB and PPSSPP falls back
        # to single-threaded mode (or refuses to load entirely).
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        super().end_headers()

    def log_message(self, format, *args):
        # Quieter output — only log non-200s
        if args[1] != "200":
            super().log_message(format, *args)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    with socketserver.TCPServer(("", PORT), CORSHandler) as httpd:
        url = f"http://localhost:{PORT}"
        print(f"[✓] Serving at {url}")
        print(f"    COOP  : same-origin")
        print(f"    COEP  : require-corp")
        print(f"    Press Ctrl+C to stop.\n")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[*] Server stopped.")
