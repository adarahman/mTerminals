"""
mTerminals Application Entry Point

Run:
    export PYTHONPATH=src
    python3 -m main
"""

import asyncio

from run_server import main as server_main


def main() -> None:
    asyncio.run(server_main())


if __name__ == "__main__":
    main()
