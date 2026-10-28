"""Send due inventory alerts. Safe to run repeatedly or as a supervised loop."""
import argparse
import time

from app import create_app
from app.shopper import process_inventory_alerts


def run_once(app):
    with app.app_context():
        sent = process_inventory_alerts()
        app.logger.info('Processed %s inventory alert(s).', sent)
    return sent


def main():
    parser = argparse.ArgumentParser(description='Process due inventory alerts.')
    parser.add_argument('--loop', action='store_true', help='Keep processing until stopped.')
    parser.add_argument('--interval', type=int, default=0, help='Seconds between loop runs.')
    args = parser.parse_args()
    app = create_app()
    interval = args.interval or int(app.config.get('ALERT_WORKER_INTERVAL_SECONDS', 300))
    while True:
        try:
            run_once(app)
        except Exception:
            app.logger.exception('Inventory alert processing failed')
            if not args.loop:
                raise
        if not args.loop:
            return
        time.sleep(max(interval, 30))


if __name__ == '__main__':
    main()
