"""Same-origin cookie endpoints and a small HTTP/WebSocket Streamlit gateway."""
import json
import ipaddress
import os
from urllib.parse import urlsplit

import tornado.httpclient
import tornado.httpserver
import tornado.ioloop
import tornado.web
import tornado.websocket
from tornado.websocket import websocket_connect

from browser_sessions import exchange_grant, identity_proof, revoke

UPSTREAM = os.getenv('IIPP_STREAMLIT_URL', 'http://127.0.0.1:8502')
COOKIE = 'iipp_session'


def _secure_cookie(handler):
    if handler.request.protocol == 'https':
        return True
    forwarded = handler.request.headers.get('X-Forwarded-Proto', '').split(',')[0].strip().lower()
    remote = handler.request.remote_ip
    for item in os.getenv('IIPP_TRUSTED_PROXY_IPS', '').split(','):
        try:
            if remote and ipaddress.ip_address(remote) in ipaddress.ip_network(item.strip(), strict=False):
                return forwarded == 'https'
        except ValueError:
            continue
    return False


def _remove_session_cookie(headers):
    cookies = headers.get('Cookie', '')
    keep = [part.strip() for part in cookies.split(';')
            if part.strip() and part.strip().split('=', 1)[0] != COOKIE]
    if keep:
        headers['Cookie'] = '; '.join(keep)
    else:
        headers.pop('Cookie', None)


class SessionHandler(tornado.web.RequestHandler):
    def data(self):
        try:
            value = json.loads(self.request.body or b'{}')
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}

    def post(self, action):
        origin = self.request.headers.get('Origin')
        if not origin or urlsplit(origin).netloc != self.request.host:
            raise tornado.web.HTTPError(403)
        token = self.get_cookie(COOKIE)
        if action == 'exchange':
            issued = exchange_grant(self.data().get('grant'))
            if not issued:
                self.set_status(401)
                self.finish({'error': 'invalid_grant'})
                return
            self.set_cookie(COOKIE, issued, httponly=True, secure=_secure_cookie(self),
                            samesite='Lax', path='/', expires_days=1)
            identity = identity_proof(issued)
        elif action == 'logout':
            revoke(token)
            self.clear_cookie(COOKIE, path='/')
            self.finish({'identity': None})
            return
        elif action == 'identity':
            identity = identity_proof(token)
            if identity is None and token:
                self.clear_cookie(COOKIE, path='/')
        else:
            raise tornado.web.HTTPError(404)
        self.set_header('Cache-Control', 'no-store')
        self.finish({'identity': identity})


class Gateway(tornado.web.RequestHandler):
    async def get(self, path=''):
        await self._proxy()

    async def head(self, path=''):
        await self._proxy()

    async def post(self, path=''):
        await self._proxy()

    async def put(self, path=''):
        await self._proxy()

    async def delete(self, path=''):
        await self._proxy()

    async def patch(self, path=''):
        await self._proxy()

    async def options(self, path=''):
        await self._proxy()

    async def _proxy(self):
        url = UPSTREAM + self.request.uri
        headers = tornado.httputil.HTTPHeaders(self.request.headers)
        for name in ('Host', 'Connection', 'Content-Length', 'X-IIPP-Session'):
            headers.pop(name, None)
        headers['Host'] = urlsplit(UPSTREAM).netloc
        _remove_session_cookie(headers)
        if headers.get('Origin'):
            headers['Origin'] = UPSTREAM
        try:
            response = await tornado.httpclient.AsyncHTTPClient().fetch(
                tornado.httpclient.HTTPRequest(url, method=self.request.method, headers=headers,
                    body=self.request.body if self.request.method not in ('GET', 'HEAD') else None,
                    follow_redirects=False, request_timeout=3600, allow_nonstandard_methods=True),
                raise_error=False)
        except tornado.httpclient.HTTPError as exc:
            if exc.response is None:
                self.set_status(502)
                self.finish('Upstream Streamlit is unavailable.')
                return
            response = exc.response
        self.set_status(response.code)
        for name, value in response.headers.get_all():
            if name.lower() not in {'transfer-encoding', 'connection', 'content-length', 'content-encoding'}:
                self.add_header(name, value)
        if response.code in (204, 304) or self.request.method == 'HEAD':
            self.finish()
        else:
            self.finish(response.body)


class StreamlitSocket(tornado.websocket.WebSocketHandler):
    def select_subprotocol(self, subprotocols):
        return 'streamlit' if 'streamlit' in subprotocols else None

    def check_origin(self, origin):
        return bool(origin and urlsplit(origin).netloc == self.request.host)

    async def open(self, path=''):
        uri = self.request.uri.replace('http://', 'ws://', 1).replace('https://', 'wss://', 1)
        target = UPSTREAM.replace('http://', 'ws://', 1).replace('https://', 'wss://', 1) + uri
        headers = tornado.httputil.HTTPHeaders(self.request.headers)
        subprotocols = [value.strip() for value in headers.get('Sec-WebSocket-Protocol', '').split(',') if value.strip()]
        headers.pop('Sec-WebSocket-Protocol', None)
        for name in ('Host', 'Connection', 'Upgrade', 'Sec-WebSocket-Key', 'Sec-WebSocket-Version', 'Sec-WebSocket-Extensions'):
            headers.pop(name, None)
        if headers.get('Origin'):
            headers['Origin'] = UPSTREAM
        _remove_session_cookie(headers)
        try:
            request = tornado.httpclient.HTTPRequest(target, headers=headers)
            self.upstream = await websocket_connect(request,
                on_message_callback=self._upstream_message, ping_interval=20, ping_timeout=20,
                subprotocols=subprotocols)
        except Exception:
            self.close(code=1011, reason='Streamlit WebSocket unavailable')

    def _upstream_message(self, message):
        if message is not None and self.ws_connection:
            self.write_message(message, binary=isinstance(message, bytes))

    def on_message(self, message):
        if getattr(self, 'upstream', None):
            self.upstream.write_message(message, binary=isinstance(message, bytes))

    def on_close(self):
        upstream = getattr(self, 'upstream', None)
        if upstream:
            upstream.close()


def main():
    app = tornado.web.Application([
        (r'/__iipp/session/(exchange|identity|logout)', SessionHandler),
        (r'/_stcore/stream/?(.*)', StreamlitSocket),
        (r'/(.*)', Gateway),
    ], websocket_ping_interval=20, websocket_ping_timeout=20, xheaders=False)
    app.listen(int(os.getenv('IIPP_PUBLIC_PORT', '8501')), address='0.0.0.0')
    tornado.ioloop.IOLoop.current().start()


if __name__ == '__main__':
    main()
