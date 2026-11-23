import sys

from app.worker.worker import cli

if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
