#!/usr/bin/env python3
import os

from dotenv import load_dotenv

load_dotenv()

from app import create_app, db
from app.models import (
    CarfaxReport, Lead, PhotoHighlightJob, Review, ServiceRecord, SiteSetting,
    User, Vehicle, VehicleImage, VehicleImageHighlight,
)

app = create_app()


@app.shell_context_processor
def make_shell_context():
    return {
        'db': db,
        'User': User,
        'Vehicle': Vehicle,
        'VehicleImage': VehicleImage,
        'VehicleImageHighlight': VehicleImageHighlight,
        'PhotoHighlightJob': PhotoHighlightJob,
        'ServiceRecord': ServiceRecord,
        'CarfaxReport': CarfaxReport,
        'SiteSetting': SiteSetting,
        'Review': Review,
        'Lead': Lead,
    }


@app.cli.command('create-admin')
def create_admin():
    """Create the default admin user if it does not exist."""
    username = os.environ.get('ADMIN_USERNAME') or app.config['ADMIN_USERNAME']
    password = os.environ.get('ADMIN_PASSWORD') or app.config['ADMIN_PASSWORD']
    if User.query.filter_by(username=username).first():
        print(f'Admin user "{username}" already exists.')
        return
    user = User(username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    print(f'Admin user "{username}" created.')


@app.cli.command('highlight-worker')
def highlight_worker_cmd():
    """Run the photo-highlight analysis worker (separate process from the web app)."""
    from app.highlight_worker import run_loop
    poll = float(os.environ.get('HIGHLIGHT_WORKER_POLL', '2'))
    lease = int(os.environ.get('HIGHLIGHT_WORKER_LEASE', '300'))
    run_loop(poll_seconds=poll, lease_seconds=lease, once=False)


@app.cli.command('highlight-enqueue-all')
def highlight_enqueue_all_cmd():
    """Queue photo highlight analysis for every vehicle image missing results."""
    from app.highlight_jobs import enqueue_vehicle_highlight_jobs
    force = os.environ.get('FORCE', '').lower() in ('1', 'true', 'yes')
    total = 0
    vehicles = Vehicle.query.order_by(Vehicle.id.asc()).all()
    for vehicle in vehicles:
        total += enqueue_vehicle_highlight_jobs(vehicle.id, force=force, only_missing=not force)
    print(f'Queued/kept {total} highlight job(s) across {len(vehicles)} vehicle(s). force={force}')


@app.cli.command('seed')
def seed_data():
    """Seed sample data for development."""
    if Vehicle.query.first():
        print('Database already has vehicles. Skipping seed.')
        return

    sample_vehicles = [
        Vehicle(
            year=2020,
            make='Honda',
            model='Accord',
            trim='EX-L',
            price=24500,
            mileage=32000,
            vin='1HGCV1F52LA123456',
            stock_number='MA2001',
            condition='used',
            title_status='clean',
            status='available',
            description='Clean CarFax, one owner, leather seats, sunroof, Apple CarPlay.',
            engine='1.5L Turbo I4',
            transmission='CVT',
            fuel_type='Gasoline',
            exterior_color='Lunar Silver Metallic',
            interior_color='Black',
            body_style='Sedan',
            drivetrain='FWD',
            mpg_city=30,
            mpg_highway=38,
            features='Leather Seats,Sunroof,Apple CarPlay,Android Auto,Backup Camera',
            seo_title='2020 Honda Accord EX-L for Sale | Marshall Auto LLC',
            seo_description='Shop this 2020 Honda Accord EX-L with 32,000 miles at Marshall Auto LLC. Clean CarFax, one owner, financing available.',
        ),
        Vehicle(
            year=2019,
            make='Toyota',
            model='RAV4',
            trim='XLE AWD',
            price=22900,
            mileage=41000,
            vin='2T3P1RFV0KW123456',
            stock_number='MA1902',
            condition='used',
            title_status='clean',
            status='available',
            description='AWD, excellent service history, lane departure warning, adaptive cruise.',
            engine='2.5L I4',
            transmission='8-Speed Automatic',
            fuel_type='Gasoline',
            exterior_color='Magnetic Gray Metallic',
            interior_color='Gray',
            body_style='SUV',
            drivetrain='AWD',
            mpg_city=26,
            mpg_highway=34,
            features='AWD,Adaptive Cruise Control,Lane Keep Assist,Backup Camera,Bluetooth',
            seo_title='2019 Toyota RAV4 XLE AWD for Sale | Marshall Auto LLC',
            seo_description='Low-mileage 2019 Toyota RAV4 XLE AWD at Marshall Auto LLC. All-wheel drive, clean history, test drive today.',
        ),
        Vehicle(
            year=2018,
            make='Ford',
            model='F-150',
            trim='XLT SuperCrew',
            price=28950,
            sale_price=27450,
            mileage=56000,
            vin='1FTEW1EP1JFA12345',
            stock_number='MA1803',
            condition='used',
            title_status='rebuilt',
            status='available',
            description='Tough, capable, and ready for work or play. Trailer tow package, running boards.',
            engine='3.5L EcoBoost V6',
            transmission='10-Speed Automatic',
            fuel_type='Gasoline',
            exterior_color='Oxford White',
            interior_color='Medium Earth Gray',
            body_style='Truck',
            drivetrain='4WD',
            mpg_city=18,
            mpg_highway=23,
            features='4WD,Trailer Tow Package,Running Boards,Sync 3,Backup Camera',
            seo_title='2018 Ford F-150 XLT SuperCrew for Sale | Marshall Auto LLC',
            seo_description='Buy this 2018 Ford F-150 XLT SuperCrew at Marshall Auto LLC. 4WD, EcoBoost V6, clean CarFax, financing available.',
        ),
    ]
    db.session.add_all(sample_vehicles)
    db.session.commit()
    for vehicle in sample_vehicles:
        vehicle.ensure_slug()
    db.session.commit()

    if not Review.query.first():
        sample_reviews = [
            Review(
                author_name='Sarah J.',
                rating=5,
                title='Great experience!',
                content='Marshall Auto made buying my Honda Accord easy and stress-free. Highly recommend!',
                is_approved=True,
                is_featured=True,
                source='Google',
            ),
            Review(
                author_name='Michael T.',
                rating=5,
                title='Honest dealership',
                content='Transparent pricing and no pressure. The team answered all my questions about the CarFax report.',
                is_approved=True,
                is_featured=True,
                source='Facebook',
            ),
            Review(
                author_name='Jessica R.',
                rating=4,
                title='Good financing options',
                content='Got approved even with fair credit. They found a rate that fit my budget.',
                is_approved=True,
                source='Google',
            ),
        ]
        db.session.add_all(sample_reviews)
        db.session.commit()
        print('Sample reviews seeded.')
    else:
        print('Reviews already exist. Skipping review seed.')

    print('Sample vehicles seeded.')


if __name__ == '__main__':
    # Use port 8080 by default to avoid macOS AirPlay Receiver on port 5000
    debug = os.environ.get('FLASK_DEBUG', '1') not in ('0', 'false', 'False')
    if app.config.get('ENV') == 'production' or os.environ.get('FLASK_ENV') == 'production':
        debug = False
    
    app.run(debug=debug, host=os.environ.get('HOST', '0.0.0.0'), port=int(os.environ.get('PORT', 8080)))