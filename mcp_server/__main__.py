import argparse
from pathlib import Path

from mcp.server.transport_security import TransportSecuritySettings

from .catalog import Catalog
from .server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the GCP IAM catalog to AI agents over MCP (streamable HTTP).")
    parser.add_argument("--dist", type=Path, default=Path("dist"), help="Directory the indexer writes")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    create_server(Catalog(args.dist)).run(
        "streamable-http",
        host=args.host,
        port=args.port,
        # In Docker the server binds 0.0.0.0 so the port can be published, but it
        # only accepts loopback Host headers. That stops a web page in the user's
        # browser from reaching it through DNS rebinding.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["localhost:*", "127.0.0.1:*"],
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
        ),
    )


if __name__ == "__main__":
    main()
