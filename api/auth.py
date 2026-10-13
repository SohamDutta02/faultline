import base64
import binascii
import secrets

from starlette.responses import PlainTextResponse

OPEN_PATHS = {"/api/health"}  # platform health checks don't carry credentials


class BasicAuth:
    """HTTP basic auth for the whole app, for hosts with no proxy in front.

    Written as plain ASGI middleware so long event streams pass straight through.
    """

    def __init__(self, app, username: str, password: str):
        self.app = app
        self.expected = (username.encode(), password.encode())

    def _authorized(self, scope) -> bool:
        header = dict(scope.get("headers") or []).get(b"authorization", b"")
        scheme, _, value = header.partition(b" ")
        if scheme.lower() != b"basic":
            return False
        try:
            user, _, password = base64.b64decode(value.strip(), validate=True).partition(b":")
        except (binascii.Error, ValueError):
            return False
        # evaluate both comparisons so timing doesn't reveal which part was wrong
        user_ok = secrets.compare_digest(user, self.expected[0])
        password_ok = secrets.compare_digest(password, self.expected[1])
        return user_ok and password_ok

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in OPEN_PATHS or self._authorized(scope):
            await self.app(scope, receive, send)
            return
        response = PlainTextResponse(
            "login required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="faultline", charset="UTF-8"'}
        )
        await response(scope, receive, send)
