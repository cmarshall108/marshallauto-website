#!/usr/bin/env python3
"""
Separate-process photo highlight worker.

Usage:
  python -m app.highlight_worker
  python -m app.highlight_worker --once
  flask highlight-worker

Runs outside the web request path so OpenCV analysis never blocks Flask/gunicorn.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

logging.basicConfig(
    level=os.environ.get('HIGHLIGHT_WORKER_LOGLEVEL', 'INFO').upper(),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger('highlight_worker')

_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    logger.info('Received signal %s — shutting down after current job', signum)
    _shutdown = True


def create_worker_app():
    from app import create_app
    return create_app()


def run_loop(poll_seconds: float = 2.0, lease_seconds: int = 300, once: bool = False):
    global _shutdown
    app = create_worker_app()
    idle_sleep = max(0.25, float(poll_seconds))
    logger.info(
        'Photo highlight worker started (poll=%.2fs lease=%ss once=%s)',
        idle_sleep, lease_seconds, once,
    )

    with app.app_context():
        from app.highlight_jobs import queue_stats, run_worker_once

        while not _shutdown:
            try:
                worked = run_worker_once(lease_seconds=lease_seconds)
            except Exception:
                logger.exception('Worker loop error')
                worked = False
                time.sleep(min(10.0, idle_sleep * 2))
                if once:
                    break
                continue

            if once:
                logger.info('Single-job mode finished (worked=%s)', worked)
                break

            if not worked:
                time.sleep(idle_sleep)
            # If we worked, immediately try next job (no sleep)

        try:
            stats = queue_stats()
            logger.info('Worker stopping. Queue stats: %s', stats)
        except Exception:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='Marshall Auto photo highlight worker')
    parser.add_argument('--once', action='store_true', help='Process at most one job then exit')
    parser.add_argument(
        '--poll',
        type=float,
        default=float(os.environ.get('HIGHLIGHT_WORKER_POLL', '2')),
        help='Idle poll interval seconds (default 2)',
    )
    parser.add_argument(
        '--lease',
        type=int,
        default=int(os.environ.get('HIGHLIGHT_WORKER_LEASE', '300')),
        help='Job lease seconds (default 300)',
    )
    args = parser.parse_args(argv)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    run_loop(poll_seconds=args.poll, lease_seconds=args.lease, once=args.once)
    return 0


if __name__ == '__main__':
    sys.exit(main())
