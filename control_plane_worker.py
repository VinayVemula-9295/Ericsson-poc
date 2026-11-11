"""Run alongside Streamlit with the same protected config and persistent DATA_DIR."""
import argparse
import time
from control_plane import run_once


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    while True:
        worked = run_once()
        if args.once:
            return
        if not worked:
            time.sleep(5)


if __name__ == '__main__':
    main()
