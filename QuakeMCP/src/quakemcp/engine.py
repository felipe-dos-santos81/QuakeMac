"""TCP client for the engine bridge: line-JSON control + framed binary."""
import itertools
import json
import socket

from .models import EngineDisconnected

SEND_TIMEOUT = 5.0
RECV_TIMEOUT = 5.0

_msg_ids = itertools.count(1)


class BridgeClient:
    """One authenticated connection to a glquake bridge endpoint."""

    def __init__(self, host, port, token):
        self.host = host
        self.port = port
        self.token = token
        try:
            self._sock = socket.create_connection(
                (host, port), timeout=SEND_TIMEOUT)
        except OSError as e:
            raise EngineDisconnected("connect %s:%s: %s" % (host, port, e))
        self._sock.settimeout(RECV_TIMEOUT)
        self._file = self._sock.makefile("rwb")

    def close(self):
        try:
            self._file.close()
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def send(self, op, **kw):
        """Write one JSON line, read one reply line. Timeout -> raise."""
        msg = {"v": 1, "auth": self.token,
               "id": kw.pop("id", "py%d" % next(_msg_ids)), "op": op}
        msg.update(kw)
        try:
            self._file.write((json.dumps(msg) + "\n").encode())
            self._file.flush()
            line = self._file.readline()
        except (OSError, socket.timeout) as e:
            raise EngineDisconnected("send %s: %s" % (op, e))
        if not line:
            raise EngineDisconnected("send %s: EOF" % op)
        try:
            return json.loads(line.decode())
        except ValueError as e:
            raise EngineDisconnected("send %s: bad reply: %s" % (op, e))

    def read_blob(self, n):
        """Read exactly N raw bytes (framed image payload, Task 7)."""
        buf = b""
        try:
            while len(buf) < n:
                chunk = self._file.read(n - len(buf))
                if not chunk:
                    raise EngineDisconnected("blob EOF at %d/%d" % (
                        len(buf), n))
                buf += chunk
        except (OSError, socket.timeout) as e:
            raise EngineDisconnected("blob: %s" % e)
        return buf
