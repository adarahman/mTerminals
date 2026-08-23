"""
mTerminals Application Entry Point

Run:

python3 -m main
"""


import sys
import logging


def setup_logging():

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )


def load_config():

    """
    Later this will come from:

    infrastructure/config.py

    For now keep it simple.
    """

    return {
        "environment": "development",
        "version": "0.1.0"
    }


def start_services(config):

    """
    Application startup sequence.

    Later this will initialize:

    - Market service
    - Broker connections
    - Decision engine
    - Risk engine
    - API server
    """

    logging.info(
        "Starting mTerminals..."
    )

    logging.info(
        "Environment: %s",
        config["environment"]
    )

    logging.info(
        "Version: %s",
        config["version"]
    )


def shutdown():

    logging.info(
        "Stopping mTerminals..."
    )


# def main():

#     setup_logging()

#     try:

#         config = load_config()

#         start_services(config)

#         logging.info(
#             "mTerminals started successfully"
#         )


#         # Temporary keep alive
#         # later replaced by server/event loop

#         while True:
#             pass


#     except KeyboardInterrupt:

#         shutdown()


#     except Exception as error:

#         logging.exception(
#             "Application failed: %s",
#             error
#         )

#         sys.exit(1)



# if __name__ == "__main__":
#     main()

"""
mTerminals startup entry point
"""

import asyncio


def main():

    print("Starting mTerminals...")


    from run_server import main as server_main

    asyncio.run(server_main())


if __name__ == "__main__":
    main()