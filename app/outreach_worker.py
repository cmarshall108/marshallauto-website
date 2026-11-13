"""Send due monthly new-car outreach. Safe to run repeatedly or on a timer."""
import argparse

from app import create_app
from app.outreach import process_monthly_outreach


def run_once(app, force=False):
    with app.app_context():
        stats = process_monthly_outreach(force=force)
        app.logger.info(
            'Monthly outreach: contacts=%s sms=%s email=%s skipped=%s',
            stats['contacts'], stats['sms'], stats['email'], stats['skipped'],
        )
    return stats


def main():
    parser = argparse.ArgumentParser(description='Process due monthly new-car outreach.')
    parser.add_argument(
        '--force',
        action='store_true',
        help='Ignore OUTREACH_ENABLED and OUTREACH_MIN_DAYS spacing.',
    )
    args = parser.parse_args()
    app = create_app()
    run_once(app, force=args.force)


if __name__ == '__main__':
    main()
