"""Startup script passed to PyMOL via -r: starts a persistent XML-RPC server.

Replaces the unreliable -R flag which logs a start message but never binds
the socket in PyMOL open-source 3.x.
"""
from xmlrpc.server import SimpleXMLRPCServer
import threading
import sys


class _PyMOLRPC:
    def do(self, command):
        from pymol import cmd
        cmd.do(str(command))
        return 1

    def ping(self):
        return 1

    def get_version(self):
        from pymol import cmd
        return str(cmd.get_version())

    def get_extent(self, selection):
        # Calls cmd.get_extent() as a real function argument (safely marshalled
        # by XML-RPC), instead of splicing the selection string into a Python
        # source block executed via do() -- that pattern let a crafted
        # selection break out of the string literal and run arbitrary code.
        from pymol import cmd
        min_corner, max_corner = cmd.get_extent(str(selection))
        return {"min": list(min_corner), "max": list(max_corner)}


def _serve(port: int = 9123) -> None:
    srv = SimpleXMLRPCServer(
        ("127.0.0.1", port),
        allow_none=True,
        logRequests=False,
    )
    srv.register_instance(_PyMOLRPC())
    print(f"xml-rpc server running on host localhost, port {port}", flush=True)
    srv.serve_forever()


_rpc_thread = threading.Thread(target=_serve, daemon=True, name="pymol-rpc")
_rpc_thread.start()
