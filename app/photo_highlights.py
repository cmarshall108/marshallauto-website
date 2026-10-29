"""
Photo highlight analysis for vehicle listing images (Carvana-style hotspots).

Primary engine: Grok vision via xAI API (XAI_API_KEY) — accurate placement,
conservative condition notes, listing-feature awareness.

Fallback: OpenCV classical CV when no API key / network / parse failure.

Coordinates are always percent of the **full source image** (0–100).
Gallery JS maps them through object-fit cover/contain to the painted box.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import mimetypes
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

ANALYSIS_VERSION = 5

# Normalized feature keywords → display metadata.
# Descriptions are shopper-facing: what the feature is, why it matters, and
# what to confirm in person. Keep them specific — never "listed on this vehicle."
FEATURE_CATALOG = {
    'apple carplay': {
        'label': 'Apple CarPlay',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'phone',
        'description': 'Plug in your iPhone and Maps, messages, and music show on the dash screen. Steering-wheel and voice controls usually work too — try a cable at the visit.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'android auto': {
        'label': 'Android Auto',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'phone',
        'description': 'Connect an Android phone for maps, calls, and music on the center screen. Confirm wired vs wireless at the visit so you know what cable, if any, to bring.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'leather seats': {
        'label': 'Leather Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'stars',
        'description': 'Leather seating is easier to wipe down than cloth and usually holds up better with kids or pets. Check bolsters and cushions for cracks or shine from wear.',
        'scenes': ('interior_cabin',),
    },
    'heated seats': {
        'label': 'Heated Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'thermometer-half',
        'description': 'Seat heaters warm up in a minute or two on cold mornings, before the cabin air is hot. Switches are usually on the center stack or climate panel — test both sides.',
        'scenes': ('interior_cabin',),
    },
    'ventilated seats': {
        'label': 'Ventilated Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'wind',
        'description': 'Ventilated seats pull air through the cushion so summer drives stay cooler. Confirm the fans run on the seats you will actually use.',
        'scenes': ('interior_cabin',),
    },
    'sunroof': {
        'label': 'Sunroof',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'The sunroof opens the cabin up and helps on short trips when you want fresh air without the windows down. Cycle it open and closed and check the seal for stains.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear', 'interior_cabin'),
    },
    'moonroof': {
        'label': 'Moonroof',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'A moonroof adds light even when it stays closed, and most tilt for ventilation. Open and close it once and look along the headliner for water marks.',
        'scenes': ('exterior_side', 'exterior_front', 'interior_cabin'),
    },
    'backup camera': {
        'label': 'Backup Camera',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'camera-video',
        'description': 'The rear camera comes on in reverse and shows guidelines for parking and trailers. Shift into reverse and confirm the image is clear, not foggy or cracked.',
        'scenes': ('interior_dash', 'exterior_rear'),
    },
    'blind spot': {
        'label': 'Blind Spot Monitor',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'eye',
        'description': 'A light in the mirror warns you when a car is sitting in the blind spot — useful on the highway and in multi-lane traffic. Confirm both mirrors light up on a test drive.',
        'scenes': ('exterior_side', 'interior_dash'),
    },
    'lane keep': {
        'label': 'Lane Keep Assist',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'signpost',
        'description': 'Lane keep assist nudges the wheel if you drift over a painted line. It is a helper, not self-driving — try it on a marked road and see how firm the steering feels.',
        'scenes': ('interior_dash',),
    },
    'adaptive cruise': {
        'label': 'Adaptive Cruise',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'Adaptive cruise holds your speed and slows down when traffic ahead slows, then picks back up. Great on the interstate — confirm the following-distance setting works.',
        'scenes': ('interior_dash',),
    },
    'navigation': {
        'label': 'Navigation',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'geo-alt',
        'description': 'Built-in navigation works without a phone and usually shows turn prompts in the cluster or on the center screen. Check that the map still updates — some need a subscription.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'bluetooth': {
        'label': 'Bluetooth',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'bluetooth',
        'description': 'Bluetooth pairs your phone for hands-free calls and streaming audio. Pair yours at the visit and confirm the microphone picks you up clearly.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'remote start': {
        'label': 'Remote Start',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'key',
        'description': 'Remote start lets you warm or cool the cabin before you get in — typically from the key fob, sometimes from a phone app. Ask which one this vehicle uses and test the range.',
        'scenes': ('exterior_front', 'exterior_side', 'interior_dash'),
    },
    'tow package': {
        'label': 'Tow Package',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A factory tow package usually adds a hitch receiver, wiring, and a higher cooling capacity. Confirm the hitch class and the door-jamb tow rating before you hook up.',
        'scenes': ('exterior_rear',),
    },
    'trailer tow': {
        'label': 'Trailer Tow',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'Trailer-tow equipment means a hitch and wiring are already on the vehicle. Match the receiver size and the listed tow rating to your trailer before you buy.',
        'scenes': ('exterior_rear',),
    },
    'running boards': {
        'label': 'Running Boards',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'distribute-horizontal',
        'description': 'Running boards give a step into a taller cabin, which helps shorter drivers and kids. Check that they are tight and not bent from a curb or off-road use.',
        'scenes': ('exterior_side',),
    },
    'alloy wheels': {
        'label': 'Alloy Wheels',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'circle',
        'description': 'Alloy wheels are lighter than steel and easier to keep looking clean. Look along the lip for curb rash — cosmetic rash is common and does not affect the drive.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear', 'wheel_closeup'),
    },
    'new tires': {
        'label': 'New Tires',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'check-circle',
        'description': 'Newer tires mean you are not buying an immediate replacement set — a full set is often several hundred dollars. Check the date code on the sidewall and that tread is even.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear', 'wheel_closeup'),
    },
    'awd': {
        'label': 'All-Wheel Drive',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shuffle',
        'description': 'All-wheel drive sends power to the wheels with grip, which helps in rain, light snow, and on gravel. It is not a substitute for winter tires on ice.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear'),
    },
    '4wd': {
        'label': 'Four-Wheel Drive',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shuffle',
        'description': 'Four-wheel drive is the better choice if you tow, haul, or leave pavement. Ask whether it is part-time 4WD or full-time, and confirm the transfer case shifts smoothly.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear'),
    },
    'panoramic': {
        'label': 'Panoramic Roof',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'A panoramic roof opens the cabin for front and rear passengers and makes the interior feel larger. Cycle the shade and glass, and look for stains along the headliner.',
        'scenes': ('exterior_side', 'exterior_front', 'interior_cabin'),
    },
    'premium audio': {
        'label': 'Premium Audio',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speaker',
        'description': 'The upgraded sound system has more speakers and cleaner volume than the base radio. Play a song you know and listen for crackle from a blown speaker.',
        'scenes': ('interior_cabin', 'interior_dash'),
    },
    'third row': {
        'label': 'Third Row Seating',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'A third row seats two or three more people for school runs and trips. Fold it flat and see how much cargo room is left — and whether adults fit back there.',
        'scenes': ('interior_cabin',),
    },
    'power liftgate': {
        'label': 'Power Liftgate',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'door-open',
        'description': 'The power liftgate opens and closes from the fob or a button, which helps when your hands are full. Test the open height if you park in a low garage.',
        'scenes': ('exterior_rear',),
    },
    'keyless': {
        'label': 'Keyless Entry',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'key',
        'description': 'With the fob in your pocket, the doors unlock as you approach and lock when you walk away. Confirm both fobs work and that the car starts with the fob left in a bag.',
        'scenes': ('exterior_side', 'exterior_front', 'interior_dash'),
    },
    'push button': {
        'label': 'Push-Button Start',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'power',
        'description': 'Press the brake and the start button — no key to turn. Keep the fob inside the cabin; if the battery in the fob dies, there is usually a backup slot or procedure.',
        'scenes': ('interior_dash',),
    },
    'dual zone': {
        'label': 'Dual-Zone Climate',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'thermometer-half',
        'description': 'Driver and passenger can set different temperatures, so nobody has to compromise on a long drive. Confirm both dials actually change the airflow.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'heated steering': {
        'label': 'Heated Steering Wheel',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'thermometer-half',
        'description': 'A heated wheel takes the chill off your hands in the first few minutes, before the cabin is warm. The button is usually on the wheel or climate panel.',
        'scenes': ('interior_dash',),
    },
    'memory seat': {
        'label': 'Memory Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'Memory seats store seat, mirror, and sometimes wheel position for two drivers. Save your setting on a numbered button so it returns after someone else drives.',
        'scenes': ('interior_cabin', 'interior_dash'),
    },
    'power seats': {
        'label': 'Power Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'Power seats adjust with a switch, including height and lumbar on most trims. Run the driver seat through its range so you know it reaches a comfortable position.',
        'scenes': ('interior_cabin',),
    },
    'bed liner': {
        'label': 'Bed Liner',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A spray-in or drop-in bed liner protects the bed from scratches, cargo, and weather. Spray-in liners usually last longer and do not trap water underneath.',
        'scenes': ('exterior_rear',),
    },
    'tonneau': {
        'label': 'Tonneau Cover',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A tonneau cover keeps the bed dry and locks cargo out of sight, and a hard cover can support light loads. Confirm it latches and that the lock works.',
        'scenes': ('exterior_rear', 'exterior_side'),
    },
    'led headlight': {
        'label': 'LED Headlights',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'LED headlights are brighter and whiter than halogen and last the life of the vehicle in most cases. Turn them on and check both sides light evenly.',
        'scenes': ('exterior_front',),
    },
    'fog light': {
        'label': 'Fog Lights',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'Fog lights throw a wide, low beam that helps in rain, fog, and on dark rural roads. Confirm both sides work — a burned-out bulb is a cheap fix, but worth knowing.',
        'scenes': ('exterior_front',),
    },
    'parking sensor': {
        'label': 'Parking Sensors',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'bullseye',
        'description': 'Sensors beep as you get close to a car, curb, or post, and usually show distance on the screen. They help in tight lots — listen for a sensor that stays silent on one corner.',
        'scenes': ('exterior_front', 'exterior_rear', 'interior_dash'),
    },
    'rear cross': {
        'label': 'Rear Cross-Traffic Alert',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'eye',
        'description': 'When you back out of a spot, this warns you about cars crossing behind you that you cannot see. It pairs with the backup camera — test it in a busy lot if you can.',
        'scenes': ('exterior_rear', 'interior_dash'),
    },
    'wireless charg': {
        'label': 'Wireless Charging',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'phone',
        'description': 'Set a compatible phone on the pad and it charges without a cable. Cases that are very thick, or MagSafe rings, sometimes block it — try your phone at the visit.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'heads-up': {
        'label': 'Heads-Up Display',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'display',
        'description': 'Speed and navigation prompts project onto the windshield so you keep your eyes up. Adjust the height so it lines up with your driving position.',
        'scenes': ('interior_dash',),
    },
    'captain chair': {
        'label': 'Captain Chairs',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'Second-row captain chairs give each passenger their own seat and an aisle to the third row. Easier for car seats than a bench — check the latch anchors.',
        'scenes': ('interior_cabin',),
    },
    'roof rack': {
        'label': 'Roof Rails',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'distribute-horizontal',
        'description': 'Roof rails are the mounting point for crossbars, a cargo box, bikes, or a kayak. Rails alone do not carry a load — you still need crossbars rated for the weight.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear'),
    },
    'crossbar': {
        'label': 'Roof Crossbars',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'distribute-horizontal',
        'description': 'Crossbars are already on the roof, so a box, bike tray, or kayak carrier bolts on without buying the bars. Check the stamped weight rating before you load them.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear'),
    },
    'spoiler': {
        'label': 'Rear Spoiler',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'The rear spoiler is a factory or dealer body piece, not a performance claim by itself. Look along the mount for cracks, extra holes, or a trunk that was drilled for an older wing.',
        'scenes': ('exterior_rear', 'exterior_side'),
    },
    'hood scoop': {
        'label': 'Hood Scoop',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'A functional scoop feeds cooler air to the engine; a decorative one is just styling. Ask which this is, and check the underside of the hood for a real opening.',
        'scenes': ('exterior_front', 'exterior_side'),
    },
    'grille': {
        'label': 'Grille',
        'category': 'detail',
        'severity': 'info',
        'icon': 'bullseye',
        'description': 'The grille is where badges, cameras, and parking sensors usually live. A mismatched grille or fresh paint around the mounts can mean front-end work — worth a question.',
        'scenes': ('exterior_front',),
    },
    'daytime running': {
        'label': 'LED Daytime Lights',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'Daytime running lights make the vehicle easier to see in traffic and are a quick tell for a newer lighting package. Confirm both sides match — one dark side is often a module, not a bulb.',
        'scenes': ('exterior_front',),
    },
    'hid headlight': {
        'label': 'HID Headlights',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'HID headlights are brighter than halogen but the bulbs and ballasts cost more when they fail. Turn them on and watch for a flicker or a color that does not match side to side.',
        'scenes': ('exterior_front',),
    },
    'halogen': {
        'label': 'Halogen Headlights',
        'category': 'detail',
        'severity': 'info',
        'icon': 'brightness-high',
        'description': 'These are halogen headlights, which are inexpensive to replace but dimmer than LED or HID. Clouding on the lens is common and a polish or a lens restore usually clears it.',
        'scenes': ('exterior_front',),
    },
    'tail light': {
        'label': 'Tail Lights',
        'category': 'detail',
        'severity': 'info',
        'icon': 'brightness-high',
        'description': 'Check that both tail lights match and that none are cracked or full of moisture. A fogged lens is often a seal, and LEDs are usually replaced as a whole assembly.',
        'scenes': ('exterior_rear',),
    },
    'brake light': {
        'label': 'Brake Lights',
        'category': 'detail',
        'severity': 'info',
        'icon': 'brightness-high',
        'description': 'Have someone press the pedal and confirm both brake lights and the center high-mount light come on. A dead side is a safety item and an inspection fail in most states.',
        'scenes': ('exterior_rear',),
    },
    'turn signal': {
        'label': 'Turn Signals',
        'category': 'detail',
        'severity': 'info',
        'icon': 'brightness-high',
        'description': 'Cycle the hazards and both turn signals. A fast blink usually means a bulb is out; on LED vehicles it can mean the whole lamp needs replacing.',
        'scenes': ('exterior_front', 'exterior_rear', 'exterior_side'),
    },
    'side mirror': {
        'label': 'Power Mirrors',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'eye',
        'description': 'Power mirrors adjust from the door switch, and many fold in for tight garages or car washes. Confirm both sides move and that the glass is not loose or cracked.',
        'scenes': ('exterior_side', 'exterior_front', 'interior_dash'),
    },
    'heated mirror': {
        'label': 'Heated Mirrors',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'thermometer-half',
        'description': 'Heated mirrors clear frost and fog with the rear defroster, so you are not scraping glass before a cold commute. The element is invisible — test it on a cool morning or ask to confirm.',
        'scenes': ('exterior_side', 'exterior_front'),
    },
    'auto dimming': {
        'label': 'Auto-Dimming Mirror',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'eye',
        'description': 'The mirror darkens when headlights hit it from behind, which cuts glare on night drives. A small sensor is usually on the mirror face — cover it with a finger and it should tint.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'rain sensing': {
        'label': 'Rain-Sensing Wipers',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'cloud-rain',
        'description': 'In auto mode the wipers start on their own when the windshield gets wet and speed up in heavier rain. Set the stalk to auto and ask how sensitive it is before a storm.',
        'scenes': ('exterior_front', 'interior_dash'),
    },
    'wiper': {
        'label': 'Wipers',
        'category': 'detail',
        'severity': 'info',
        'icon': 'cloud-rain',
        'description': 'Run the washers and wipers. Streaking or a chatter means the blades are due — a cheap fix, but a dry reservoir or a dead pump is worth catching now.',
        'scenes': ('exterior_front',),
    },
    'power window': {
        'label': 'Power Windows',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'window',
        'description': 'Run every window fully down and back up, including the rears. A slow window or one that needs a slam to seal is usually a regulator, which is a common repair.',
        'scenes': ('exterior_side', 'interior_cabin', 'interior_dash'),
    },
    'one-touch': {
        'label': 'One-Touch Windows',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'window',
        'description': 'A full pull or push on the switch drops or raises the window without holding it. Express-up on all four doors is the convenient version — confirm which doors have it.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'power lock': {
        'label': 'Power Locks',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'key',
        'description': 'All doors should lock and unlock from the driver switch and the fob. A door that only works from its own switch usually needs an actuator.',
        'scenes': ('interior_dash', 'exterior_side'),
    },
    'sliding door': {
        'label': 'Power Sliding Doors',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'door-open',
        'description': 'Power sliding doors open from the fob or a button, which matters with kids and groceries. Cycle both sides and listen for a grind — tracks need to stay clean to last.',
        'scenes': ('exterior_side',),
    },
    'hands-free': {
        'label': 'Hands-Free Liftgate',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'door-open',
        'description': 'A kick under the rear bumper opens the liftgate when your hands are full, as long as the fob is on you. Try it once — the sensor zone is smaller than people expect.',
        'scenes': ('exterior_rear',),
    },
    'power tailgate': {
        'label': 'Power Tailgate',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'door-open',
        'description': 'The tailgate opens and closes from the fob or a button. Test the open height if you park in a garage, and confirm it latches fully so it does not pop on the road.',
        'scenes': ('exterior_rear',),
    },
    'tailgate': {
        'label': 'Tailgate',
        'category': 'detail',
        'severity': 'info',
        'icon': 'door-open',
        'description': 'Open and close the tailgate and check the cables or struts hold it. A sagging gate or a latch that needs a slam is a wear item, not a reason to walk away.',
        'scenes': ('exterior_rear',),
    },
    'hitch': {
        'label': 'Trailer Hitch',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A receiver is already mounted, so you can tow or carry a bike rack without a shop visit. Confirm the hitch class and the door-jamb tow rating before you hook up.',
        'scenes': ('exterior_rear',),
    },
    'trailer wiring': {
        'label': 'Trailer Wiring',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A 4-pin or 7-pin plug is on the vehicle, so trailer lights can connect without splicing. Match the plug to your trailer and test the lights before a trip.',
        'scenes': ('exterior_rear',),
    },
    '7-pin': {
        'label': '7-Pin Trailer Plug',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A 7-pin plug supports trailer lights plus brakes and a charge line, which most campers and heavier trailers need. A 4-pin trailer will need an adapter.',
        'scenes': ('exterior_rear',),
    },
    '4-pin': {
        'label': '4-Pin Trailer Plug',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A 4-pin plug runs basic trailer lights. It will not power electric trailer brakes — those need a 7-pin plug and a brake controller.',
        'scenes': ('exterior_rear',),
    },
    'brake controller': {
        'label': 'Trailer Brake Controller',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A brake controller is already installed, which is required to safely tow most trailers with electric brakes. Confirm it powers up and is not just a blank housing.',
        'scenes': ('interior_dash',),
    },
    'spray-in': {
        'label': 'Spray-In Bed Liner',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A spray-in liner is bonded to the bed, so it does not trap water the way a drop-in plastic liner can. Look for peeling at the wheel wells and bed floor.',
        'scenes': ('exterior_rear',),
    },
    'drop-in': {
        'label': 'Drop-In Bed Liner',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A drop-in liner protects the bed but can hide rust if water sits under it. Lift an edge if you can and look at the bed floor before you buy.',
        'scenes': ('exterior_rear',),
    },
    'toolbox': {
        'label': 'Bed Toolbox',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A crossover or side toolbox keeps tools locked in the bed. Confirm you get the key, and that the box is bolted down rather than just sitting on the rails.',
        'scenes': ('exterior_rear', 'exterior_side'),
    },
    'fifth wheel': {
        'label': 'Fifth-Wheel Prep',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'Fifth-wheel or gooseneck prep means the bed is reinforced for a hitch. Confirm the puck system or rails are complete and ask for the tow rating on the door jamb.',
        'scenes': ('exterior_rear',),
    },
    'gooseneck': {
        'label': 'Gooseneck Prep',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'Gooseneck prep puts a reinforced mount in the bed for heavier trailers. Make sure the ball pocket or pucks are included, not just the under-bed brackets.',
        'scenes': ('exterior_rear',),
    },
    'side step': {
        'label': 'Side Steps',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'distribute-horizontal',
        'description': 'Side steps make a taller truck or SUV easier to climb into. Power steps should deploy when the door opens — confirm they do not hang down while driving.',
        'scenes': ('exterior_side',),
    },
    'mud flap': {
        'label': 'Mud Flaps',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shield',
        'description': 'Mud flaps keep rocks and spray off the rockers and the vehicle behind you. Factory flaps are a small plus; missing flaps on a work truck are normal wear.',
        'scenes': ('exterior_side', 'exterior_rear'),
    },
    'fender flare': {
        'label': 'Fender Flares',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'circle',
        'description': 'Flares cover wider tires and protect the paint from gravel. Look under the edge for trapped dirt and for screw holes that mean they were added later.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear'),
    },
    'bug shield': {
        'label': 'Bug Shield',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shield',
        'description': 'A hood shield deflects bugs and small stones off the leading edge of the hood. Check that it is not sandblasted opaque and that the hood paint under it is intact.',
        'scenes': ('exterior_front',),
    },
    'window visor': {
        'label': 'Window Visors',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'cloud-rain',
        'description': 'Visors let you crack the windows in rain and cut side-window glare. In-channel visors look cleaner; tape-on visors can leave residue if you ever remove them.',
        'scenes': ('exterior_side',),
    },
    'sunshade': {
        'label': 'Rear Sunshades',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'moon',
        'description': 'Manual or power rear shades cut glare for kids in the back seat. Cycle them and confirm they retract fully so they do not block the rear view.',
        'scenes': ('interior_cabin', 'exterior_rear'),
    },
    'all-weather': {
        'label': 'All-Weather Floor Mats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shield',
        'description': 'Rubber mats keep salt, mud, and spills off the carpet, which matters at resale. Lift them and check the carpet underneath is dry, not stained or moldy.',
        'scenes': ('interior_cabin',),
    },
    'floor mat': {
        'label': 'Floor Mats',
        'category': 'detail',
        'severity': 'info',
        'icon': 'shield',
        'description': 'Floor mats should be the right shape for this vehicle and clipped down so they cannot bunch under the pedals. Lift them and look for wet carpet.',
        'scenes': ('interior_cabin',),
    },
    'cargo mat': {
        'label': 'Cargo Mat',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shield',
        'description': 'A cargo mat protects the load floor from groceries, pets, and muddy gear. It is a small thing that saves the carpet you would otherwise be cleaning.',
        'scenes': ('interior_cabin', 'exterior_rear'),
    },
    'cargo cover': {
        'label': 'Cargo Cover',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shield',
        'description': 'A retractable cargo cover hides what is in the back from outside. Confirm it is present and retracts — these go missing often and are annoying to replace.',
        'scenes': ('interior_cabin', 'exterior_rear'),
    },
    'cargo net': {
        'label': 'Cargo Net',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shield',
        'description': 'A cargo net keeps bags from sliding around. Check the anchors in the load floor are intact if you plan to use one.',
        'scenes': ('interior_cabin', 'exterior_rear'),
    },
    'spare tire': {
        'label': 'Spare Tire',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'circle',
        'description': 'A spare is under the vehicle or in the cargo well, so a flat does not leave you waiting on a tow. Check whether it is a full-size spare or a temporary donut, and that the jack is there.',
        'scenes': ('exterior_rear', 'interior_cabin'),
    },
    'full-size spare': {
        'label': 'Full-Size Spare',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'circle',
        'description': 'A full-size spare matches the other tires, so you can keep driving instead of limping on a donut. Confirm it is inflated and the same size as the mounted tires.',
        'scenes': ('exterior_rear', 'exterior_side'),
    },
    'donut': {
        'label': 'Temporary Spare',
        'category': 'detail',
        'severity': 'info',
        'icon': 'circle',
        'description': 'This is a temporary spare, meant for low speed and short distance only. Budget a real tire if one of the mounted ones is already worn.',
        'scenes': ('exterior_rear', 'interior_cabin'),
    },
    'tire inflator': {
        'label': 'Tire Inflator Kit',
        'category': 'detail',
        'severity': 'info',
        'icon': 'circle',
        'description': 'There is no spare — a sealant kit and compressor are the flat-tire plan. That will not fix a sidewall cut, so know where the kit is before you need it.',
        'scenes': ('interior_cabin', 'exterior_rear'),
    },
    'chrome wheel': {
        'label': 'Chrome Wheels',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'circle',
        'description': 'Chrome wheels are a dress-up item. Pitting and peeling chrome are cosmetic but expensive to redo — look at the inner barrel, where brake dust eats the finish first.',
        'scenes': ('exterior_side', 'wheel_closeup', 'exterior_front', 'exterior_rear'),
    },
    'steel wheel': {
        'label': 'Steel Wheels',
        'category': 'detail',
        'severity': 'info',
        'icon': 'circle',
        'description': 'Steel wheels with covers are durable and cheap to replace if you curb one. They are heavier than alloys and the covers can hide rust on the wheel itself.',
        'scenes': ('exterior_side', 'wheel_closeup', 'exterior_front', 'exterior_rear'),
    },
    'all-terrain': {
        'label': 'All-Terrain Tires',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'circle',
        'description': 'All-terrain tires handle gravel, light mud, and snow better than highway tires, with some extra road noise. Check the date code — rubber ages even if the tread looks deep.',
        'scenes': ('exterior_side', 'wheel_closeup', 'exterior_front', 'exterior_rear'),
    },
    'mud-terrain': {
        'label': 'Mud-Terrain Tires',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'circle',
        'description': 'Mud-terrain tires are built for off-road traction. Expect more noise and a firmer ride on the highway, and confirm they are not oversized for the stock suspension.',
        'scenes': ('exterior_side', 'wheel_closeup'),
    },
    'tpms': {
        'label': 'Tire Pressure Monitor',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'TPMS warns you when a tire drops pressure, which prevents a slow leak from becoming a blowout. A light that stays on usually means a sensor battery, not a bad tire.',
        'scenes': ('interior_dash', 'wheel_closeup'),
    },
    'lift kit': {
        'label': 'Lift Kit',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'arrow-up',
        'description': 'A lift adds ground clearance and room for larger tires. Ask what was done (spacers vs new shocks) and listen for a clunk over bumps — a cheap lift shows up in the ride.',
        'scenes': ('exterior_side', 'exterior_front'),
    },
    'leveling kit': {
        'label': 'Leveling Kit',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'arrow-up',
        'description': 'A leveling kit raises the front to match the rear, which levels the stance and fits slightly larger tires. Confirm the alignment was done after it was installed.',
        'scenes': ('exterior_side', 'exterior_front'),
    },
    'lowered': {
        'label': 'Lowered Suspension',
        'category': 'detail',
        'severity': 'info',
        'icon': 'arrow-down',
        'description': 'The suspension has been lowered. That can improve looks and handling, but check for rubbing, a harsh ride, and whether the alignment is current.',
        'scenes': ('exterior_side',),
    },
    'air suspension': {
        'label': 'Air Suspension',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'arrow-up',
        'description': 'Air suspension can raise for clearance and lower for loading or highway comfort. Cycle the height settings and listen for a compressor that runs constantly — that usually means a leak.',
        'scenes': ('exterior_side', 'interior_dash'),
    },
    'adaptive suspension': {
        'label': 'Adaptive Suspension',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'The dampers firm up in sport mode and soften for the daily drive. Switch modes on a test drive — if the ride never changes, a shock or the controller may be in a fault.',
        'scenes': ('interior_dash', 'exterior_side'),
    },
    'skid plate': {
        'label': 'Skid Plates',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shield',
        'description': 'Skid plates protect the oil pan, transfer case, and fuel tank on rough roads. Look for fresh scrapes, which tell you it has actually been used off pavement.',
        'scenes': ('exterior_front', 'exterior_side'),
    },
    'off-road': {
        'label': 'Off-Road Package',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'signpost',
        'description': 'An off-road package usually adds skid plates, different tires, and sometimes locking differentials or hill-descent control. Ask which pieces this one actually has.',
        'scenes': ('exterior_side', 'exterior_front', 'interior_dash'),
    },
    'locking differential': {
        'label': 'Locking Differential',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shuffle',
        'description': 'A locker forces both wheels on an axle to turn together, which is what gets you out of mud or a ditch. Confirm the switch engages and the light comes on.',
        'scenes': ('interior_dash', 'exterior_side'),
    },
    'limited slip': {
        'label': 'Limited-Slip Differential',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shuffle',
        'description': 'A limited-slip differential sends power to the wheel with grip, which helps in rain, snow, and when one tire is on ice. It works on its own — there is often no switch.',
        'scenes': ('exterior_side', 'exterior_rear'),
    },
    'hill descent': {
        'label': 'Hill Descent Control',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'arrow-down',
        'description': 'Hill descent holds a slow speed down a steep grade so you are not riding the brakes. Useful off-road and on steep gravel driveways — the button is usually near the drive-mode controls.',
        'scenes': ('interior_dash',),
    },
    'hill start': {
        'label': 'Hill-Start Assist',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'arrow-up',
        'description': 'On a hill the brakes hold for a second after you lift off, so you do not roll back while moving to the gas. You will feel it on the test drive if there is any grade nearby.',
        'scenes': ('interior_dash',),
    },
    'tow haul': {
        'label': 'Tow/Haul Mode',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'Tow/haul mode holds gears longer and downshifts sooner so the transmission is not hunting while towing. Use it with a trailer; leaving it on empty just raises rpm.',
        'scenes': ('interior_dash',),
    },
    'integrated brake': {
        'label': 'Integrated Trailer Brake',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'The trailer brake controller is built into the dash, so you do not need an aftermarket box to run electric trailer brakes. Confirm the gain adjustment is in the menu.',
        'scenes': ('interior_dash',),
    },
    'bed light': {
        'label': 'Bed Lighting',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'A bed light lets you load and unload after dark without a flashlight. Turn it on and confirm it is not just a cargo lamp in the cab.',
        'scenes': ('exterior_rear',),
    },
    '120v': {
        'label': 'Bed Power Outlet',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'power',
        'description': 'A 120-volt outlet in the bed runs tools and pumps without a generator. Check the watt limit on the sticker before you plug in a saw or a compressor.',
        'scenes': ('exterior_rear',),
    },
    'power outlet': {
        'label': 'Power Outlets',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'power',
        'description': '12-volt and USB ports keep phones, coolers, and inverters powered. Count the ports you will actually use and confirm the rear ones stay live with the key off if you need them to.',
        'scenes': ('interior_dash', 'interior_cabin', 'exterior_rear'),
    },
    'inverter': {
        'label': 'Household Power Outlet',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'power',
        'description': 'A household-style outlet runs a laptop charger or a small tool from the vehicle. Check the watt rating — most built-in inverters will not run a space heater or a large saw.',
        'scenes': ('interior_cabin', 'interior_dash', 'exterior_rear'),
    },
    'wifi': {
        'label': 'In-Car Wi-Fi',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'wifi',
        'description': 'Built-in Wi-Fi can share a connection with passengers, but it usually needs its own data plan. Confirm whether a subscription is active or if you would be paying for it.',
        'scenes': ('interior_dash',),
    },
    'satellite radio': {
        'label': 'Satellite Radio',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'broadcast',
        'description': 'SiriusXM hardware is in the radio, so you can add a subscription without new equipment. A trial may already be expired — the tuner still works once you subscribe.',
        'scenes': ('interior_dash',),
    },
    'harman': {
        'label': 'Harman Kardon Audio',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speaker',
        'description': 'Harman Kardon is the factory premium system. It should stay clear as you turn it up — a rattle at moderate volume is a loose speaker, not the amp.',
        'scenes': ('interior_cabin', 'interior_dash'),
    },
    'bang & olufsen': {
        'label': 'Bang & Olufsen Audio',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speaker',
        'description': 'Bang & Olufsen is a top-trim sound system and one of the more expensive options to have from the factory. Confirm every speaker plays, including the dash tweeters.',
        'scenes': ('interior_cabin', 'interior_dash'),
    },
    'subwoofer': {
        'label': 'Subwoofer',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speaker',
        'description': 'A factory or added subwoofer fills in bass the door speakers cannot. If it was added later, ask whether the wiring is clean — a hacked install can drain the battery.',
        'scenes': ('interior_cabin', 'exterior_rear'),
    },
    'amplifier': {
        'label': 'Amplifier',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speaker',
        'description': 'An external amp drives more speakers at cleaner volume. Aftermarket amps should be fused and mounted, not loose in the cargo area.',
        'scenes': ('interior_cabin', 'exterior_rear'),
    },
    'touchscreen': {
        'label': 'Touchscreen',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'display',
        'description': 'The center screen runs audio, phone, and the backup camera. Tap through a few menus and confirm it responds quickly — a delaminating or dead screen is an expensive repair.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'digital cluster': {
        'label': 'Digital Instrument Cluster',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'A digital cluster can show the map, speed, and driver assists in front of you instead of a fixed gauge set. Page through the views on the wheel and confirm none are dead pixels.',
        'scenes': ('interior_dash',),
    },
    'instrument cluster': {
        'label': 'Instrument Cluster',
        'category': 'detail',
        'severity': 'info',
        'icon': 'speedometer',
        'description': 'Look for warning lights that stay on after startup. A tire, ABS, or check-engine light is information, not automatically a deal-breaker — ask what it means on this vehicle.',
        'scenes': ('interior_dash',),
    },
    'steering wheel control': {
        'label': 'Steering-Wheel Controls',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'disc',
        'description': 'Volume, cruise, and phone buttons are on the wheel, so you are not reaching for the screen. Press each one — worn buttons that only work sometimes are a common annoyance.',
        'scenes': ('interior_dash',),
    },
    'paddle shift': {
        'label': 'Paddle Shifters',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'Paddles let you pick a gear without moving your hands off the wheel, useful on hills and when passing. They do not make an automatic into a manual — the computer can still upshift.',
        'scenes': ('interior_dash',),
    },
    'telescoping': {
        'label': 'Telescoping Wheel',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'arrows-expand',
        'description': 'The wheel moves in and out as well as up and down, so you can sit at a safe distance from the airbag and still reach it. Power tilt is a plus if more than one person drives.',
        'scenes': ('interior_dash',),
    },
    'tilt wheel': {
        'label': 'Tilt Wheel',
        'category': 'detail',
        'severity': 'info',
        'icon': 'arrows-expand',
        'description': 'The wheel tilts so you can see the gauges and sit comfortably. If it only tilts and does not telescope, taller and shorter drivers have less room to get the position right.',
        'scenes': ('interior_dash',),
    },
    'leather wheel': {
        'label': 'Leather-Wrapped Wheel',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'disc',
        'description': 'A leather-wrapped wheel is easier on the hands than hard plastic, especially in heat and cold. Shine on the rim is normal wear; cracks or a peeling wrap are not.',
        'scenes': ('interior_dash',),
    },
    'wood trim': {
        'label': 'Wood Trim',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'stars',
        'description': 'Wood or wood-look trim is a higher-trim interior cue. Real wood can crack in extreme heat; plastic woodgrain wearing through at the edges is the thing to look for.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'ambient light': {
        'label': 'Ambient Lighting',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'Ambient lighting outlines the cabin at night and on many vehicles you can pick the color. It is cosmetic, but it is also how you tell a higher trim in the dark.',
        'scenes': ('interior_cabin', 'interior_dash'),
    },
    'tri-zone': {
        'label': 'Tri-Zone Climate',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'thermometer-half',
        'description': 'A third climate zone lets the rear passengers set their own temperature, which matters with kids in the back. Confirm the rear controls work and the vents blow cold.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'rear climate': {
        'label': 'Rear Climate Controls',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'thermometer-half',
        'description': 'Rear controls let back-seat passengers adjust their own airflow instead of asking you to change it. Check that the rear blower actually runs.',
        'scenes': ('interior_cabin',),
    },
    'automatic climate': {
        'label': 'Automatic Climate Control',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'thermometer-half',
        'description': 'Set a temperature and the system handles fan speed and mode. If it only blows hot or only cold, the blend door is a known failure on many models — test both extremes.',
        'scenes': ('interior_dash',),
    },
    'climate control': {
        'label': 'Climate Control',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'thermometer-half',
        'description': 'Set the temperature and confirm it blows cold within a minute and hot after that. Weak A/C on a warm day is one of the more expensive comfort repairs.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'air conditioning': {
        'label': 'Air Conditioning',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'snow',
        'description': 'A/C should blow genuinely cold within a minute, not just cool. A musty smell at startup often means the evaporator drain is slow, which is worth mentioning.',
        'scenes': ('interior_dash',),
    },
    'rear air': {
        'label': 'Rear Air Conditioning',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'snow',
        'description': 'Rear A/C keeps the back seat cool on long summer trips instead of relying on the front vents. Confirm the rear blower runs and the vents are not blocked by the seats.',
        'scenes': ('interior_cabin',),
    },
    'heated and cooled': {
        'label': 'Heated and Ventilated Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'thermometer-half',
        'description': 'These seats heat in winter and pull air through the cushion in summer. Test both functions on the seats you will use — one side failing is common and not always cheap.',
        'scenes': ('interior_cabin',),
    },
    'massaging': {
        'label': 'Massaging Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'A massaging seat cycles the lumbar bolsters on long drives. Turn it on and confirm it moves — the button can be present after the pump has failed.',
        'scenes': ('interior_cabin',),
    },
    'lumbar': {
        'label': 'Power Lumbar',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'Power lumbar lets you set lower-back support instead of living with the seat as it came. Run it in and out — a dead lumbar is a comfort issue you will feel every day.',
        'scenes': ('interior_cabin',),
    },
    'bucket seats': {
        'label': 'Bucket Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'Front bucket seats hold you in place better than a bench and usually include a center console with storage. Check both adjust through their full range.',
        'scenes': ('interior_cabin',),
    },
    'bench seat': {
        'label': 'Front Bench Seat',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'A front bench seats three across, which is rare and useful for work trucks. The middle position often has a lap belt only — check the belts before you plan on using it daily.',
        'scenes': ('interior_cabin',),
    },
    '60/40': {
        'label': 'Split-Folding Seat',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'A 60/40 split lets you fold one side for cargo and still seat a passenger on the other. Fold both sides and confirm they latch back upright.',
        'scenes': ('interior_cabin',),
    },
    'fold flat': {
        'label': 'Fold-Flat Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'The seats fold flat for lumber, a bike, or a dog crate. Try the release levers — a seat that will not latch upright is a daily annoyance.',
        'scenes': ('interior_cabin',),
    },
    'power third': {
        'label': 'Power Third Row',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'The third row folds with a button, which matters when you are loading cargo and do not want to climb in. Cycle it and listen for a strain in the motor.',
        'scenes': ('interior_cabin', 'exterior_rear'),
    },
    'latch': {
        'label': 'LATCH Child Anchors',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'LATCH anchors let you click a car seat in without the adult belt. Find the lower anchors and the tether — some third-row positions do not have them.',
        'scenes': ('interior_cabin',),
    },
    'child seat': {
        'label': 'Car-Seat Anchors',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'Lower anchors and a tether are how a child seat locks in. Check the positions you will actually use — the center of a bench is often belt-only.',
        'scenes': ('interior_cabin',),
    },
    'power sunshade': {
        'label': 'Power Sunshade',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'moon',
        'description': 'The shade closes over the glass so the cabin does not bake in the sun. Run it fully closed — a shade that stops halfway usually needs a reset or a new motor.',
        'scenes': ('interior_cabin',),
    },
    'convertible top': {
        'label': 'Convertible Top',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'Cycle the top fully down and back up, and look at the rear window and the seams. A slow cycle, a torn seam, or a window that is cloudy are the expensive items.',
        'scenes': ('exterior_side', 'exterior_rear', 'interior_cabin'),
    },
    'soft top': {
        'label': 'Soft Top',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'Check the soft top for cracks at the folds and a rear window that is still clear. A replacement top is a four-figure job, so the condition matters more than the color.',
        'scenes': ('exterior_side', 'exterior_rear'),
    },
    'hardtop': {
        'label': 'Removable Hardtop',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'A hardtop quiets the cabin and locks more securely than a soft top. Confirm the hardware and any storage for the panels come with the vehicle.',
        'scenes': ('exterior_side', 'exterior_rear'),
    },
    't-top': {
        'label': 'T-Tops',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'T-tops come out for open-air driving and should seal when they are in. Look for water stains on the seats and carpet — leaks are the known issue.',
        'scenes': ('exterior_side', 'interior_cabin'),
    },
    'smart key': {
        'label': 'Smart Key',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'key',
        'description': 'The smart key unlocks the doors and starts the vehicle without coming out of your pocket. Ask for every fob — replacing a lost one is often a few hundred dollars.',
        'scenes': ('interior_dash', 'exterior_side'),
    },
    'proximity': {
        'label': 'Proximity Key',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'key',
        'description': 'The doors respond when the fob is nearby, and the vehicle starts with the fob still in your pocket. Test both fobs if two are included.',
        'scenes': ('exterior_side', 'interior_dash'),
    },
    'alarm': {
        'label': 'Factory Alarm',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shield',
        'description': 'The factory alarm sounds if a door opens without the fob. Aftermarket alarms can be touchy and drain batteries — ask which one this is if a light is blinking.',
        'scenes': ('interior_dash', 'exterior_front'),
    },
    'immobilizer': {
        'label': 'Engine Immobilizer',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shield',
        'description': 'The immobilizer will not let the engine run without the programmed key, which is basic theft protection. A vehicle that cranks and dies may have a key-chip issue.',
        'scenes': ('interior_dash',),
    },
    'front sensor': {
        'label': 'Front Parking Sensors',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'bullseye',
        'description': 'Front sensors help in tight lots and when nosing up to a wall you cannot see over the hood. They are the small circles in the bumper — confirm they beep.',
        'scenes': ('exterior_front', 'interior_dash'),
    },
    '360 camera': {
        'label': 'Surround-View Camera',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'camera-video',
        'description': 'A 360 camera stitches front, side, and rear views into a top-down picture, which makes parking and curbs much easier. Check that no camera is foggy or out of the stitch.',
        'scenes': ('interior_dash', 'exterior_front', 'exterior_rear'),
    },
    'lane departure': {
        'label': 'Lane Departure Warning',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'signpost',
        'description': 'Lane departure warning beeps or vibrates if you drift over a line without signaling. It is a warning, not steering assist — try it on a marked road.',
        'scenes': ('interior_dash',),
    },
    'lane centering': {
        'label': 'Lane Centering',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'signpost',
        'description': 'Lane centering actively holds the vehicle between the lines on the highway, as long as your hands stay on the wheel. It will hand control back in a tight curve.',
        'scenes': ('interior_dash',),
    },
    'cruise control': {
        'label': 'Cruise Control',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'Cruise holds a set speed so you are not managing the pedal on the highway. Standard cruise does not slow for traffic — that is adaptive cruise, which is a different feature.',
        'scenes': ('interior_dash',),
    },
    'forward collision': {
        'label': 'Forward Collision Warning',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'exclamation-triangle',
        'description': 'The system warns, and on many vehicles brakes, if it thinks you are about to hit the car ahead. Keep the sensor area on the windshield or grille clean or it will shut itself off.',
        'scenes': ('interior_dash', 'exterior_front'),
    },
    'automatic emergency': {
        'label': 'Automatic Emergency Braking',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'exclamation-triangle',
        'description': 'Automatic emergency braking can hit the brakes if you do not react to a stopped car. It is a backup, not a promise — confirm the dash is not showing a system fault.',
        'scenes': ('interior_dash', 'exterior_front'),
    },
    'pedestrian': {
        'label': 'Pedestrian Detection',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'Pedestrian detection can warn or brake if someone steps out in front of you at low speed. The camera is usually at the top of the windshield — keep that area clear.',
        'scenes': ('interior_dash', 'exterior_front'),
    },
    'cross-traffic': {
        'label': 'Cross-Traffic Alert',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'eye',
        'description': 'Cross-traffic alert watches for cars passing behind you as you back out. Some vehicles also watch the front when you nose out of a blind spot.',
        'scenes': ('exterior_rear', 'exterior_front', 'interior_dash'),
    },
    'parking assist': {
        'label': 'Parking Assist',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'bullseye',
        'description': 'Parking assist steers the vehicle into a parallel or perpendicular spot while you manage the pedals. Try it once — you still have to watch for curbs and poles.',
        'scenes': ('interior_dash', 'exterior_side'),
    },
    'auto park': {
        'label': 'Automatic Parking',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'bullseye',
        'description': 'The vehicle finds a spot and steers itself in while you handle the brake. It is slower than parking yourself, but useful in tight city spots.',
        'scenes': ('interior_dash',),
    },
    'voice command': {
        'label': 'Voice Commands',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'mic',
        'description': 'Voice controls let you place calls, change music, or enter a destination without looking down. Try a command — older systems are picky about phrasing.',
        'scenes': ('interior_dash',),
    },
    'homelink': {
        'label': 'HomeLink',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'house',
        'description': 'HomeLink buttons on the mirror or visor can open your garage and gate without a separate remote. They need to be programmed to your opener — the buttons being there is the feature.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'auto headlights': {
        'label': 'Automatic Headlights',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'Automatic headlights turn on at dusk and in tunnels so you are not driving with daytime lights only. Leave the switch in auto and confirm they actually come on.',
        'scenes': ('interior_dash', 'exterior_front'),
    },
    'auto high beam': {
        'label': 'Automatic High Beams',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'High beams turn themselves down when they see an oncoming car, then come back up. Useful on dark roads — the camera is at the rearview mirror, so keep that glass clean.',
        'scenes': ('interior_dash', 'exterior_front'),
    },
    'projector': {
        'label': 'Projector Headlights',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'Projector headlights put a sharper cutoff on the beam so you can see farther without blinding oncoming traffic. Clouding inside the lens is harder to polish out than on a reflector lamp.',
        'scenes': ('exterior_front',),
    },
    'led taillight': {
        'label': 'LED Tail Lights',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'LED tail lights are brighter and react faster than bulbs. If one side is out, the repair is usually the whole assembly, not a $5 bulb.',
        'scenes': ('exterior_rear',),
    },
    'sequencer': {
        'label': 'Sequential Turn Signals',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'Sequential signals sweep across the lamp instead of blinking a single bulb. If a section is dark, the lamp is typically replaced as a unit.',
        'scenes': ('exterior_rear', 'exterior_front'),
    },
    'tow hook': {
        'label': 'Tow Hooks',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'Frame-mounted tow hooks are how you get pulled out if you are stuck. They are not a recovery point for a snatch strap unless they are rated for it — ask before you yank.',
        'scenes': ('exterior_front', 'exterior_rear'),
    },
    'winch': {
        'label': 'Winch',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'A winch can pull the vehicle out of mud or up a grade. Check that the cable is not frayed and that you get the remote — and confirm it is wired to a real battery connection.',
        'scenes': ('exterior_front',),
    },
    'light bar': {
        'label': 'Light Bar',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'An LED light bar adds a lot of light off-road. It is not legal to use on most public roads facing forward — confirm the switch works and the wiring is fused.',
        'scenes': ('exterior_front', 'exterior_side'),
    },
    'aftermarket wheel': {
        'label': 'Aftermarket Wheels',
        'category': 'detail',
        'severity': 'info',
        'icon': 'circle',
        'description': 'These are not the factory wheels. Confirm the tire size still matches the speedometer and that the lug hardware is correct for this vehicle.',
        'scenes': ('exterior_side', 'wheel_closeup'),
    },
    'cold air intake': {
        'label': 'Cold Air Intake',
        'category': 'detail',
        'severity': 'info',
        'icon': 'wind',
        'description': 'An aftermarket intake is visible under the hood. It can add sound more than power — check the filter is clamped and not sitting where it can suck in water.',
        'scenes': ('other',),
    },
    'dual exhaust': {
        'label': 'Dual Exhaust',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'Dual tips are often a real dual system on V6 and V8 models, and sometimes just styling on a single pipe. A performance sound is fine; a tick or a leak under the cab is not.',
        'scenes': ('exterior_rear',),
    },
    'cat-back': {
        'label': 'Aftermarket Exhaust',
        'category': 'detail',
        'severity': 'info',
        'icon': 'speedometer',
        'description': 'The exhaust has been replaced behind the catalytic converter. Louder is a preference; a check-engine light or a removed converter is a legal and inspection problem.',
        'scenes': ('exterior_rear', 'exterior_side'),
    },
    'turbo': {
        'label': 'Turbocharged',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'A turbo makes a smaller engine pull harder, especially with a load or at altitude. Listen for a whistle that turns into a grind — that can be the turbo, not the intake.',
        'scenes': ('other', 'interior_dash'),
    },
    'supercharger': {
        'label': 'Supercharged',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'A supercharger adds power with a belt-driven blower. Ask whether it is factory and whether the engine internals were left stock — that changes how hard you should drive it.',
        'scenes': ('other',),
    },
    'diesel': {
        'label': 'Diesel Engine',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'fuel-pump',
        'description': 'A diesel is the better tow and highway engine if you put on miles, and it wants highway runs to stay healthy. Ask about DEF, the last fuel-filter change, and any delete work.',
        'scenes': ('other', 'exterior_side', 'interior_dash'),
    },
    'hybrid': {
        'label': 'Hybrid System',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'battery-charging',
        'description': 'The hybrid system shuts the gas engine off at stops and helps it accelerate, which is where the fuel savings come from. Ask for any battery-health readout the car can show.',
        'scenes': ('interior_dash', 'other'),
    },
    'plug-in': {
        'label': 'Plug-In Hybrid',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'battery-charging',
        'description': 'A plug-in hybrid can cover a short commute on electricity, then run as a regular hybrid. Ask the electric-only range and whether the charge cable is included.',
        'scenes': ('interior_dash', 'exterior_side', 'other'),
    },
    'electric vehicle': {
        'label': 'Electric Drive',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'battery-charging',
        'description': 'This is a battery-electric vehicle — no gas engine, and home charging covers most daily driving. Ask the current range estimate and whether a home charger is required for your commute.',
        'scenes': ('interior_dash', 'exterior_side'),
    },
    'charging port': {
        'label': 'Charge Port',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'battery-charging',
        'description': 'This is where the vehicle plugs in. Confirm the port door opens, the connector type (J1772, NACS, or CCS), and that a charge cable comes with it.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear'),
    },
    'regenerative': {
        'label': 'Regenerative Braking',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'battery-charging',
        'description': 'Lifting off the pedal slows the vehicle and puts energy back in the battery. Strong regen feels like one-pedal driving — try the settings before you assume the brakes are grabbing.',
        'scenes': ('interior_dash',),
    },
    'one-pedal': {
        'label': 'One-Pedal Driving',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'In one-pedal mode the vehicle slows hard when you lift off, so city driving barely uses the brake pedal. It is adjustable — set it where it feels natural before a highway merge.',
        'scenes': ('interior_dash',),
    },
    'transfer case': {
        'label': 'Transfer Case',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shuffle',
        'description': 'The transfer case is what sends power to the front axle. Shift through the ranges at low speed and listen for a grind — that is not normal.',
        'scenes': ('interior_dash', 'other'),
    },
    'manual transmission': {
        'label': 'Manual Transmission',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'gear',
        'description': 'A manual gives you the gear changes and is cheaper to rebuild than most automatics, but the clutch is a wear item. A high bite point or a slip under load means a clutch is due.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'automatic transmission': {
        'label': 'Automatic Transmission',
        'category': 'detail',
        'severity': 'info',
        'icon': 'gear',
        'description': 'Shifts should be smooth, not a flare in rpm or a harsh bang between gears. A delayed engagement into drive or reverse when cold is worth a pre-purchase inspection.',
        'scenes': ('interior_dash',),
    },
    'column shift': {
        'label': 'Column Shifter',
        'category': 'detail',
        'severity': 'info',
        'icon': 'gear',
        'description': 'The shifter is on the column, which frees the center seat on a bench-seat truck. Confirm park engages firmly and the indicator matches the gear you selected.',
        'scenes': ('interior_dash',),
    },
    'rotary shift': {
        'label': 'Rotary Shifter',
        'category': 'detail',
        'severity': 'info',
        'icon': 'gear',
        'description': 'A rotary dial selects gear instead of a lever. Make sure it returns to park on its own when you shut the vehicle off, which most of these do.',
        'scenes': ('interior_dash',),
    },
    'push-button shift': {
        'label': 'Push-Button Shifter',
        'category': 'detail',
        'severity': 'info',
        'icon': 'gear',
        'description': 'Gear selection is a row of buttons. Confirm each one lights and that the vehicle will not shut off out of park without the brake set.',
        'scenes': ('interior_dash',),
    },
    'sport mode': {
        'label': 'Sport Mode',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'Sport mode holds lower gears and firms the throttle, which helps passing and hills. It uses more fuel — it is a button, not a different vehicle.',
        'scenes': ('interior_dash',),
    },
    'drive mode': {
        'label': 'Drive Modes',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'Drive modes change throttle, shifting, and sometimes steering or ride. Cycle them on the test drive so you know eco is not stuck on.',
        'scenes': ('interior_dash',),
    },
    'eco mode': {
        'label': 'Eco Mode',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'Eco mode softens the throttle to save fuel. If the vehicle feels lazy, check that eco is not selected before you assume something is wrong.',
        'scenes': ('interior_dash',),
    },
    'snow mode': {
        'label': 'Snow Mode',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'snow',
        'description': 'Snow mode starts in a higher gear and softens the throttle so the tires do not spin. It helps in slush; it does not replace winter tires on ice.',
        'scenes': ('interior_dash',),
    },
    'tow mode': {
        'label': 'Tow Mode',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'Tow mode changes shift points so the transmission stays cooler and more predictable with a trailer. Turn it on only when you are actually towing.',
        'scenes': ('interior_dash',),
    },
    'auto hold': {
        'label': 'Auto Hold',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'pause',
        'description': 'Auto hold keeps the vehicle stopped at a light after you lift off the brake, until you press the gas. Useful in stop-and-go — the button is usually near the parking brake.',
        'scenes': ('interior_dash',),
    },
    'electronic parking': {
        'label': 'Electronic Parking Brake',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'pause',
        'description': 'A switch sets the parking brake instead of a pedal or a handle, and many release automatically when you pull away. Confirm the light comes on and goes off.',
        'scenes': ('interior_dash',),
    },
    'parking brake': {
        'label': 'Parking Brake',
        'category': 'detail',
        'severity': 'info',
        'icon': 'pause',
        'description': 'Set and release the parking brake. A handle that pulls to the top, or a pedal with no resistance, means the cables or shoes need attention.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'traction control': {
        'label': 'Traction Control',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shuffle',
        'description': 'Traction control cuts power when a wheel spins, which helps in rain and snow. You can usually turn it off for sand or a stuck tire — confirm the button works.',
        'scenes': ('interior_dash',),
    },
    'stability control': {
        'label': 'Stability Control',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shuffle',
        'description': 'Stability control brakes individual wheels if the vehicle starts to slide. Leave it on for the road. A warning light that stays on is an inspection item.',
        'scenes': ('interior_dash',),
    },
    'front camera': {
        'label': 'Front Camera',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'camera-video',
        'description': 'A front camera shows the curb and the space you cannot see over the hood. It usually comes on at low speed or when you press a button near the screen.',
        'scenes': ('exterior_front', 'interior_dash'),
    },
    'side camera': {
        'label': 'Side Cameras',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'camera-video',
        'description': 'Side cameras fill in the blind area along the doors and are part of a surround view. A foggy mirror-mounted camera shows up as a blank panel on the screen.',
        'scenes': ('exterior_side', 'interior_dash'),
    },
    'wireless apple': {
        'label': 'Wireless Apple CarPlay',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'phone',
        'description': 'CarPlay connects without a cable once the phone is paired, so you are not plugging in every trip. Confirm it reconnects on its own after you walk away and come back.',
        'scenes': ('interior_dash',),
    },
    'wireless android': {
        'label': 'Wireless Android Auto',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'phone',
        'description': 'Android Auto connects over Bluetooth and Wi-Fi, with no cable. Pair your phone at the visit — some vehicles need a one-time USB connection first.',
        'scenes': ('interior_dash',),
    },
}

IMPERFECTION_META = {
    'scratch': {
        'label': 'Possible Scratch',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-triangle',
        'description': 'A thin mark in the clear coat. Run a fingernail across it — if it does not catch, a polish often hides it. If it catches, ask whether it is in the paint.',
    },
    'scuff': {
        'label': 'Possible Scuff',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-triangle',
        'description': 'A rub mark, often from a bumper or parking block. Many scuffs are in the clear coat and buff out. Check whether the paint color is still even underneath.',
    },
    'ding': {
        'label': 'Possible Ding',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-circle',
        'description': 'A small dent. Paintless dent repair can often push these out if the paint is not cracked. Look at it in daylight from a low angle.',
    },
    'chip': {
        'label': 'Possible Chip',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-circle',
        'description': 'A chip down to primer or metal will rust if it stays open, especially on a hood edge. Touch-up paint stops that. Confirm the chip is not already rusting.',
    },
    'wear': {
        'label': 'Wear Spot',
        'category': 'imperfection',
        'severity': 'info',
        'icon': 'info-circle',
        'description': 'Normal use shows here — bolster shine, wheel rash, or a worn pedal. It does not mean a mechanical problem. Compare it with the mileage and the rest of the car.',
    },
    'tire_good': {
        'label': 'Tire Tread',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'check-circle',
        'description': 'Tread still looks healthy from this angle, so you are likely not replacing tires right away. A penny test or measuring the 32nds at the visit confirms remaining life.',
    },
    'tire_worn': {
        'label': 'Check Tire Wear',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-triangle',
        'description': 'Tread looks low from this angle. Budget for a set if the wear bars are close to flush — uneven wear can also point to an alignment issue.',
    },
    'alloy_wheel': {
        'label': 'Alloy Wheel',
        'category': 'detail',
        'severity': 'info',
        'icon': 'circle',
        'description': 'Alloy wheel shown up close. Light curb rash on the lip is cosmetic; a bend or a bubble in the tire sidewall is the thing to flag.',
    },
    'infotainment': {
        'label': 'Touchscreen',
        'category': 'detail',
        'severity': 'info',
        'icon': 'display',
        'description': 'This is the center screen for audio, phone, and backup camera. Tap through the menus and confirm it responds — a slow or dead screen is an expensive repair.',
    },
    'leather_surface': {
        'label': 'Seat Condition',
        'category': 'detail',
        'severity': 'info',
        'icon': 'stars',
        'description': 'Driver seat shown up close. Bolster shine and small creases are normal; splits, stains, or a sagging cushion are worth a closer look.',
    },
}

ICON_ALLOWLIST = {
    'phone', 'stars', 'thermometer-half', 'wind', 'brightness-high', 'camera-video',
    'eye', 'signpost', 'speedometer', 'geo-alt', 'bluetooth', 'key', 'truck',
    'distribute-horizontal', 'circle', 'check-circle', 'shuffle', 'speaker',
    'people', 'door-open', 'power', 'exclamation-triangle', 'exclamation-circle',
    'info-circle', 'display', 'bullseye', 'cloud-rain', 'window', 'shield', 'moon',
    'wifi', 'broadcast', 'disc', 'arrows-expand', 'snow', 'house', 'mic', 'gear',
    'fuel-pump', 'battery-charging', 'arrow-up', 'arrow-down', 'pause',
}

SCENE_VALUES = {
    'exterior_front', 'exterior_side', 'exterior_rear', 'wheel_closeup',
    'interior_dash', 'interior_cabin', 'other',
}


@dataclass
class HighlightCandidate:
    x_pct: float
    y_pct: float
    label: str
    category: str = 'detail'  # feature | imperfection | detail
    description: str = ''
    icon: str = 'info-circle'
    severity: str = 'info'  # positive | info | caution | issue
    confidence: float = 0.5
    source: str = 'auto'
    order_index: int = 0
    meta: dict = field(default_factory=dict)

    def clamped(self) -> 'HighlightCandidate':
        self.x_pct = round(min(97.0, max(3.0, float(self.x_pct))), 2)
        self.y_pct = round(min(97.0, max(3.0, float(self.y_pct))), 2)
        self.confidence = round(min(1.0, max(0.0, float(self.confidence))), 3)
        return self

    def to_dict(self) -> dict:
        self.clamped()
        data = asdict(self)
        data.pop('meta', None)
        return data


def _import_cv2():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        return cv2, np
    except ImportError as exc:
        raise RuntimeError(
            'OpenCV is required for photo highlights fallback. Install with: '
            'pip install opencv-python-headless numpy'
        ) from exc


def parse_feature_tokens(features_text: Optional[str]) -> List[str]:
    if not features_text:
        return []
    raw = re.split(r'[,;\n|/]+', features_text)
    return [t.strip().lower() for t in raw if t and t.strip()]


# Extra listing phrases that should resolve to an existing catalog entry.
# Kept out of FEATURE_CATALOG so display labels stay unique.
FEATURE_ALIASES = {
    'carplay': 'apple carplay',
    'cooled seats': 'ventilated seats',
    'cooled seat': 'ventilated seats',
    'sun roof': 'sunroof',
    'moon roof': 'moonroof',
    'rearview camera': 'backup camera',
    'rear view camera': 'backup camera',
    'backup cam': 'backup camera',
    'rear camera': 'backup camera',
    'blind-spot': 'blind spot',
    'adaptive cruise control': 'adaptive cruise',
    'navigation system': 'navigation',
    'nav system': 'navigation',
    'remote starter': 'remote start',
    'running board': 'running boards',
    'alloy wheel': 'alloy wheels',
    'awd system': 'awd',
    'all-wheel': 'awd',
    'all wheel': 'awd',
    'four-wheel': '4wd',
    'four wheel': '4wd',
    '4x4': '4wd',
    '4 x 4': '4wd',
    'panoramic roof': 'panoramic',
    'premium sound': 'premium audio',
    'third-row': 'third row',
    'keyless entry': 'keyless',
    'keyless start': 'push button',
    'push-button': 'push button',
    'pushbutton': 'push button',
    'dual-zone': 'dual zone',
    'dual zone climate': 'dual zone',
    'heated wheel': 'heated steering',
    'memory seats': 'memory seat',
    'bed cover': 'tonneau',
    'tonneau cover': 'tonneau',
    'led headlights': 'led headlight',
    'led headlamps': 'led headlight',
    'fog lights': 'fog light',
    'fog lamps': 'fog light',
    'fog lamp': 'fog light',
    'backup sensors': 'parking sensor',
    'rear cross traffic': 'rear cross',
    'rear cross-traffic': 'rear cross',
    'head-up': 'heads-up',
    'hud': 'heads-up',
    'captain chairs': 'captain chair',
    'captain\'s chairs': 'captain chair',
    'roof rail': 'roof rack',
    'roof rails': 'roof rack',
    'window deflector': 'window visor',
    'window deflectors': 'window visor',
    'all terrain': 'all-terrain',
    'tire pressure': 'tpms',
    'wi-fi': 'wifi',
    'sirius': 'satellite radio',
    'siriusxm': 'satellite radio',
    'infotainment': 'touchscreen',
    'surround view': '360 camera',
    'around view': '360 camera',
    '360 camera': '360 camera',
    '360-degree': '360 camera',
    '360 degree': '360 camera',
    'garage door': 'homelink',
    '6-speed': 'manual transmission',
    'usb port': 'power outlet',
    'usb ports': 'power outlet',
    'usb-c': 'power outlet',
    'usb': 'power outlet',
}


def match_feature_catalog(features_text: Optional[str]) -> List[Tuple[str, dict]]:
    tokens = parse_feature_tokens(features_text)
    blob = ' | '.join(tokens)
    matched = []
    seen_labels = set()
    keys = sorted(FEATURE_CATALOG.keys(), key=len, reverse=True)
    alias_keys = sorted(FEATURE_ALIASES.keys(), key=len, reverse=True)

    def _consider(key: str, meta: dict) -> None:
        if meta['label'] in seen_labels:
            return
        seen_labels.add(meta['label'])
        matched.append((key, meta))

    for key in keys:
        hit = key in blob or any(key == t or key in t for t in tokens)
        if hit:
            _consider(key, FEATURE_CATALOG[key])
    for alias in alias_keys:
        # Word-ish match so "360" does not fire inside unrelated numbers,
        # and "usb" does not require its own catalog row.
        if alias in blob or any(alias == t or alias in t for t in tokens):
            target = FEATURE_ALIASES[alias]
            if target in FEATURE_CATALOG:
                _consider(target, FEATURE_CATALOG[target])
    return matched


def _resize_for_analysis(cv2, np, bgr, max_side: int = 960):
    h, w = bgr.shape[:2]
    scale = 1.0
    if max(h, w) > max_side:
        scale = max_side / float(max(h, w))
        bgr = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return bgr, scale


def classify_scene(cv2, np, bgr) -> str:
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    top = gray[: max(1, h // 3), :]
    bottom = gray[int(h * 0.65):, :]
    top_mean = float(np.mean(top))
    bottom_mean = float(np.mean(bottom))
    overall = float(np.mean(gray))

    upper_hsv = hsv[: max(1, h // 3), :]
    sky_mask = (
        (upper_hsv[:, :, 0] >= 90) & (upper_hsv[:, :, 0] <= 130) &
        (upper_hsv[:, :, 1] >= 40) & (upper_hsv[:, :, 2] >= 120)
    )
    sky_ratio = float(np.mean(sky_mask)) if sky_mask.size else 0.0

    seat_mask = (
        ((hsv[:, :, 0] <= 25) | (hsv[:, :, 0] >= 160)) &
        (hsv[:, :, 1] >= 20) & (hsv[:, :, 1] <= 180) &
        (hsv[:, :, 2] >= 25) & (hsv[:, :, 2] <= 160)
    )
    seat_ratio = float(np.mean(seat_mask))

    center = gray[int(h * 0.25):int(h * 0.75), int(w * 0.25):int(w * 0.75)]
    bright_center = float(np.mean(center > 180)) if center.size else 0.0

    circles = _detect_wheels(cv2, np, bgr)
    wheel_count = len(circles)

    if wheel_count >= 1 and seat_ratio < 0.22 and (sky_ratio > 0.03 or overall > 85):
        if wheel_count == 1:
            x, y, r = circles[0]
            if r > min(h, w) * 0.16 and y > h * 0.35:
                return 'wheel_closeup'
        if sky_ratio > 0.06 and top_mean >= bottom_mean - 5:
            edges = cv2.Canny(gray, 60, 140)
            left = float(np.mean(edges[:, : w // 2]))
            right = float(np.mean(edges[:, w // 2:]))
            symmetry = 1.0 - min(1.0, abs(left - right) / (max(left, right, 1e-3)))
            if symmetry > 0.75 and wheel_count >= 2:
                return 'exterior_front' if bright_center < 0.15 else 'exterior_rear'
            return 'exterior_side'
        return 'exterior_side'

    if seat_ratio > 0.20 and sky_ratio < 0.04:
        if bright_center > 0.10:
            return 'interior_dash'
        return 'interior_cabin'

    if sky_ratio > 0.05:
        return 'exterior_side' if wheel_count else 'exterior_front'

    if bright_center > 0.12 and overall < 115:
        return 'interior_dash'

    return 'other'


def _detect_wheels(cv2, np, bgr) -> List[Tuple[int, int, int]]:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (9, 9), 1.5)
    min_r = max(14, int(min(h, w) * 0.055))
    max_r = max(min_r + 4, int(min(h, w) * 0.26))
    circles = cv2.HoughCircles(
        blur,
        cv2.HOUGH_GRADIENT,
        dp=1.25,
        minDist=int(min(h, w) * 0.22),
        param1=130,
        param2=42,
        minRadius=min_r,
        maxRadius=max_r,
    )
    found = []
    if circles is not None:
        for c in np.round(circles[0]).astype(int):
            x, y, r = int(c[0]), int(c[1]), int(c[2])
            if y < h * 0.32 or y > h * 0.95:
                continue
            roi = gray[max(0, y - r): min(h, y + r), max(0, x - r): min(w, x + r)]
            if roi.size and float(np.mean(roi)) > 175:
                continue
            if roi.size and float(np.std(roi)) < 12:
                continue
            found.append((x, y, r))
    found.sort(key=lambda t: t[2], reverse=True)
    return found[:3]


def _wheel_highlights(cv2, np, bgr, circles) -> List[HighlightCandidate]:
    out: List[HighlightCandidate] = []
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    for idx, (x, y, r) in enumerate(circles[:2]):
        yy, xx = np.ogrid[:h, :w]
        dist = np.sqrt((xx - x) ** 2 + (yy - y) ** 2)
        ring = (dist >= r * 0.62) & (dist <= r * 0.95)
        if not np.any(ring):
            continue
        ring_vals = gray[ring]
        contrast = float(np.std(ring_vals))
        mean_v = float(np.mean(ring_vals))
        if contrast >= 34 and mean_v < 135:
            meta = IMPERFECTION_META['tire_good']
            conf = min(0.86, 0.58 + contrast / 140.0)
        elif contrast < 12 and mean_v > 40:
            meta = IMPERFECTION_META['tire_worn']
            conf = 0.6
        else:
            continue
        if conf < 0.58:
            continue
        out.append(HighlightCandidate(
            x_pct=(x / w) * 100.0,
            y_pct=(y / h) * 100.0,
            label=meta['label'],
            category=meta['category'],
            description=meta['description'],
            icon=meta['icon'],
            severity=meta['severity'],
            confidence=conf,
            order_index=10 + idx,
            meta={'kind': 'wheel', 'radius_px': r},
        ))
    return out


def _detect_screen_region(cv2, np, bgr) -> Optional[Tuple[float, float, float]]:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    y0, y1 = int(h * 0.18), int(h * 0.70)
    x0, x1 = int(w * 0.28), int(w * 0.72)
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    blur = cv2.GaussianBlur(roi, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    candidates = []
    for mat in (th, cv2.bitwise_not(th)):
        contours, _ = cv2.findContours(mat, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            roi_area = float(roi.shape[0] * roi.shape[1] or 1)
            if area < roi_area * 0.035 or area > roi_area * 0.40:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            aspect = bw / float(bh or 1)
            if aspect < 1.2 or aspect > 2.6:
                continue
            fill = area / float(bw * bh or 1)
            if fill < 0.55:
                continue
            patch = roi[y:y + bh, x:x + bw]
            if patch.size and float(np.mean(patch)) < 90:
                continue
            cx = x0 + x + bw / 2.0
            cy = y0 + y + bh / 2.0
            conf = min(0.9, 0.5 + fill * 0.35 + (0.1 if 1.4 <= aspect <= 2.2 else 0.0))
            candidates.append((cx, cy, conf, area))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[2], t[3]), reverse=True)
    cx, cy, conf, _ = candidates[0]
    if conf < 0.58:
        return None
    return (cx / w) * 100.0, (cy / h) * 100.0, conf


def _detect_seat_regions(cv2, np, bgr) -> List[Tuple[float, float, float]]:
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    masks = [
        cv2.inRange(hsv, (0, 0, 15), (180, 80, 85)),
        cv2.inRange(hsv, (5, 45, 40), (25, 200, 170)),
        cv2.inRange(hsv, (0, 0, 70), (180, 35, 160)),
    ]
    mask = masks[0]
    for m in masks[1:]:
        mask = cv2.bitwise_or(mask, m)
    mask[: int(h * 0.22), :] = 0
    mask[int(h * 0.90):, :] = 0
    mask = cv2.medianBlur(mask, 9)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    min_area = h * w * 0.05
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bh < h * 0.14 or bw < w * 0.12:
            continue
        cx = x + bw / 2.0
        cy = y + bh * 0.45
        conf = min(0.8, 0.45 + area / float(h * w))
        regions.append(((cx / w) * 100.0, (cy / h) * 100.0, conf))
    regions.sort(key=lambda t: t[2], reverse=True)
    return regions[:2]


def _detect_imperfections(cv2, np, bgr, scene: str) -> List[HighlightCandidate]:
    if scene.startswith('interior'):
        return _detect_interior_wear(cv2, np, bgr)

    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, _a, _b = cv2.split(lab)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 70, 160)

    horizontal = cv2.morphologyEx(
        edges, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (21, 1)),
    )
    vertical = cv2.morphologyEx(
        edges, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 21)),
    )
    thin = cv2.bitwise_or(horizontal, vertical)

    blur = cv2.GaussianBlur(l, (31, 31), 0)
    highpass = cv2.absdiff(l, blur)
    thr = max(18, int(float(np.mean(highpass)) + 2.4 * float(np.std(highpass))))
    _, spots = cv2.threshold(highpass, thr, 255, cv2.THRESH_BINARY)
    spots = cv2.morphologyEx(spots, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=2)

    thin[: int(h * 0.18), :] = 0
    thin[int(h * 0.88):, :] = 0
    thin[:, : int(w * 0.08)] = 0
    thin[:, int(w * 0.92):] = 0
    spots[: int(h * 0.18), :] = 0
    spots[int(h * 0.88):, :] = 0

    for (cx, cy, r) in _detect_wheels(cv2, np, bgr):
        cv2.circle(thin, (cx, cy), int(r * 1.25), 0, -1)
        cv2.circle(spots, (cx, cy), int(r * 1.25), 0, -1)

    out: List[HighlightCandidate] = []

    def _collect(mask, kind: str, min_area: int, max_items: int):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        scored = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > h * w * 0.02:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            aspect = max(bw, bh) / float(min(bw, bh) or 1)
            cx = x + bw / 2.0
            cy = y + bh / 2.0
            if cy < h * 0.15 or cy > h * 0.86:
                continue
            score = area * (1.2 if aspect > 4.0 else 1.0)
            scored.append((score, cx, cy, aspect, area))
        scored.sort(key=lambda t: t[0], reverse=True)
        for score, cx, cy, aspect, area in scored[:max_items]:
            meta = IMPERFECTION_META[kind]
            if kind == 'scratch' and aspect < 3.2:
                meta = IMPERFECTION_META['scuff']
            if kind == 'ding' and area < 55:
                meta = IMPERFECTION_META['chip']
            conf = min(0.72, 0.48 + math.log1p(area) / 14.0)
            if conf < 0.55:
                continue
            out.append(HighlightCandidate(
                x_pct=(cx / w) * 100.0,
                y_pct=(cy / h) * 100.0,
                label=meta['label'],
                category=meta['category'],
                description=meta['description'],
                icon=meta['icon'],
                severity=meta['severity'],
                confidence=conf,
                order_index=40,
                meta={'kind': kind, 'area': float(area)},
            ))

    _collect(thin, 'scratch', min_area=max(40, int(h * w * 0.00025)), max_items=1)
    _collect(spots, 'ding', min_area=max(28, int(h * w * 0.00018)), max_items=1)
    return out


def _detect_interior_wear(cv2, np, bgr) -> List[HighlightCandidate]:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (21, 21), 0)
    hp = cv2.absdiff(gray, blur)
    thr = max(22, int(float(np.mean(hp)) + 2.5 * float(np.std(hp))))
    _, mask = cv2.threshold(hp, thr, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8), iterations=2)
    mask[: int(h * 0.18), :] = 0
    mask[int(h * 0.92):, :] = 0
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    scored = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < h * w * 0.0012 or area > h * w * 0.08:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        scored.append((area, x + bw / 2.0, y + bh / 2.0))
    scored.sort(reverse=True)
    meta = IMPERFECTION_META['wear']
    for area, cx, cy in scored[:1]:
        conf = min(0.68, 0.48 + area / float(h * w) * 6)
        if conf < 0.55:
            continue
        out.append(HighlightCandidate(
            x_pct=(cx / w) * 100.0,
            y_pct=(cy / h) * 100.0,
            label=meta['label'],
            category=meta['category'],
            description=meta['description'],
            icon=meta['icon'],
            severity=meta['severity'],
            confidence=conf,
            order_index=45,
        ))
    return out


def _feature_placements(scene: str, matched_features, screen_pt, seat_pts, wheels, w, h) -> List[HighlightCandidate]:
    """Place listing features only when a visual anchor exists for that scene."""
    out: List[HighlightCandidate] = []
    used_points: List[Tuple[float, float]] = []

    def accept(pt: Tuple[float, float]) -> bool:
        if any(math.hypot(pt[0] - u[0], pt[1] - u[1]) < 12 for u in used_points):
            return False
        used_points.append(pt)
        return True

    seat_pref = [(p[0], p[1]) for p in seat_pts]
    screen_pref = [(screen_pt[0], screen_pt[1])] if screen_pt else []
    wheel_pref = [((x / w) * 100.0, (y / h) * 100.0) for x, y, r in wheels]

    # Scene-typical anchors so the OpenCV fallback can still mark equipment
    # Grok would place by sight. Points are percent of the full frame.
    front = scene == 'exterior_front'
    rear = scene == 'exterior_rear'
    side = scene == 'exterior_side'
    cabin = scene.startswith('interior')
    anchors = {
        'headlight': [(28.0, 58.0), (72.0, 58.0)] if front else [],
        'fog': [(22.0, 72.0), (78.0, 72.0)] if front else [],
        'grille': [(50.0, 52.0)] if front else [],
        'camera': [(50.0, 42.0)] if rear else ([(50.0, 48.0)] if front else []),
        'hitch': [(50.0, 78.0)] if rear else [],
        'tailgate': [(50.0, 48.0)] if rear else [],
        'bed': [(50.0, 55.0)] if rear else [],
        'roof': [(50.0, 16.0)] if (front or side or rear) else ([(50.0, 18.0)] if cabin else []),
        'step': [(38.0, 78.0), (62.0, 78.0)] if side else [],
        'mirror': [(18.0, 42.0)] if side else ([(22.0, 40.0)] if front else []),
        'charge': [(62.0, 52.0)] if (side or rear) else [],
        'wheel_ctrl': [(42.0, 62.0)] if cabin else [],
        'cluster': [(38.0, 48.0)] if scene == 'interior_dash' else [],
        'climate': [(50.0, 72.0)] if scene == 'interior_dash' else [],
        'speaker': [(78.0, 42.0), (22.0, 55.0)] if cabin else [],
        'mat': [(50.0, 82.0)] if cabin else [],
    }

    def _bucket(label_l: str) -> str:
        if any(s in label_l for s in (
            'carplay', 'android', 'navigation', 'bluetooth', 'infotainment',
            'touchscreen', 'apple', 'wifi', 'hotspot', 'wireless charg',
        )):
            return 'screen'
        if any(s in label_l for s in ('steering', 'paddle', 'cruise', 'lane', 'push-button', 'push button', 'head-up', 'hud')):
            return 'wheel_ctrl'
        if any(s in label_l for s in ('cluster', 'gauge', 'odometer')):
            return 'cluster'
        if any(s in label_l for s in ('climate', 'heated wheel', 'dual-zone', 'dual zone', 'remote start')):
            return 'climate'
        if 'seat' in label_l or 'leather' in label_l or 'captain' in label_l or 'third row' in label_l or 'memory' in label_l:
            return 'seat'
        if 'speaker' in label_l or 'audio' in label_l or 'sound' in label_l:
            return 'speaker'
        if 'sunroof' in label_l or 'moonroof' in label_l or 'panoramic' in label_l or 'roof' in label_l:
            return 'roof'
        if 'headlight' in label_l or 'headlamp' in label_l or 'drl' in label_l or 'daytime' in label_l:
            return 'headlight'
        if 'fog' in label_l:
            return 'fog'
        if 'grille' in label_l or 'badge' in label_l:
            return 'grille'
        if 'camera' in label_l or 'sensor' in label_l or 'blind' in label_l:
            return 'camera'
        if 'hitch' in label_l or 'tow' in label_l or 'trailer' in label_l:
            return 'hitch'
        if 'liftgate' in label_l or 'tailgate' in label_l or 'spoiler' in label_l:
            return 'tailgate'
        if 'bed' in label_l or 'tonneau' in label_l or 'liner' in label_l or 'toolbox' in label_l:
            return 'bed'
        if 'step' in label_l or 'running board' in label_l or 'nerf' in label_l:
            return 'step'
        if 'mirror' in label_l:
            return 'mirror'
        if 'charg' in label_l or 'plug-in' in label_l or 'electric' in label_l:
            return 'charge'
        if 'tire' in label_l or 'wheel' in label_l or 'awd' in label_l or '4wd' in label_l or '4x4' in label_l:
            return 'wheel'
        if 'mat' in label_l:
            return 'mat'
        return ''

    for idx, (key, meta) in enumerate(matched_features):
        scenes = meta.get('scenes') or ()
        if scenes and scene not in scenes:
            continue

        preferred: List[Tuple[float, float]] = []
        label_l = meta['label'].lower()
        bucket = _bucket(label_l)
        if bucket == 'screen':
            preferred.extend(screen_pref)
        elif bucket == 'seat':
            preferred.extend(seat_pref)
        elif bucket == 'wheel':
            preferred.extend(wheel_pref)
        elif bucket and anchors.get(bucket):
            preferred.extend(anchors[bucket])
        elif scene.startswith('exterior') and wheel_pref and bucket == '':
            # Unknown exterior item: do not dump it on a wheel.
            preferred = []
        elif screen_pref and bucket == '':
            preferred = []

        # Require a real anchor — no random jitter
        if not preferred:
            continue

        placed = False
        for p in preferred:
            if accept(p):
                out.append(HighlightCandidate(
                    x_pct=p[0],
                    y_pct=p[1],
                    label=meta['label'],
                    category=meta['category'],
                    description=meta['description'],
                    icon=meta.get('icon', 'stars'),
                    severity=meta.get('severity', 'positive'),
                    confidence=0.72 if scene in scenes else 0.58,
                    order_index=idx,
                    meta={'feature_key': key},
                ))
                placed = True
                break
        if not placed:
            continue
    return out


def _nms_highlights(
    items: List[HighlightCandidate],
    min_dist_pct: float = 12.0,
    max_items: int = 5,
    min_confidence: float = 0.55,
) -> List[HighlightCandidate]:
    severity_rank = {'positive': 3, 'info': 2, 'caution': 2, 'issue': 1}
    category_rank = {'feature': 3, 'detail': 1, 'imperfection': 2}

    def sort_key(h: HighlightCandidate):
        return (
            category_rank.get(h.category, 0),
            severity_rank.get(h.severity, 0),
            h.confidence,
        )

    filtered = [h.clamped() for h in items if h.confidence >= min_confidence]
    ordered = sorted(filtered, key=sort_key, reverse=True)
    kept: List[HighlightCandidate] = []
    for h in ordered:
        if any(math.hypot(h.x_pct - k.x_pct, h.y_pct - k.y_pct) < min_dist_pct for k in kept):
            continue
        if any(k.label == h.label for k in kept):
            continue
        kept.append(h)
        if len(kept) >= max_items:
            break
    kept.sort(key=lambda h: (0 if h.category == 'feature' else 1, h.y_pct, h.x_pct))
    for i, h in enumerate(kept):
        h.order_index = i
    return kept


# --- Grok / xAI vision ---------------------------------------------------------

def _engine_preference() -> str:
    return (os.environ.get('PHOTO_HIGHLIGHTS_ENGINE') or 'grok').strip().lower()


def _xai_api_key() -> str:
    return (
        os.environ.get('XAI_API_KEY')
        or os.environ.get('GROK_API_KEY')
        or ''
    ).strip()


def _grok_model() -> str:
    return (
        os.environ.get('PHOTO_HIGHLIGHTS_GROK_MODEL')
        or os.environ.get('XAI_MODEL')
        or 'grok-4.7'
    ).strip()


def _image_to_data_url(image_path: str, max_side: int = 1280) -> str:
    """Encode image as JPEG data URL, downscaling large photos for API cost/latency."""
    mime, _ = mimetypes.guess_type(image_path)
    try:
        from PIL import Image
        import io
        with Image.open(image_path) as im:
            im = im.convert('RGB')
            w, h = im.size
            if max(w, h) > max_side:
                scale = max_side / float(max(w, h))
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format='JPEG', quality=85, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode('ascii')
            return f'data:image/jpeg;base64,{b64}'
    except Exception:
        with open(image_path, 'rb') as f:
            raw = f.read()
        # Cap raw payload ~4MB
        if len(raw) > 4_000_000:
            raise ValueError('Image too large and Pillow resize failed')
        b64 = base64.b64encode(raw).decode('ascii')
        mt = mime if mime in ('image/jpeg', 'image/png') else 'image/jpeg'
        return f'data:{mt};base64,{b64}'


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    # Strip markdown fences
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    # Find outermost { ... }
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start:end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    return None


def _normalize_icon(icon: Optional[str], category: str, severity: str) -> str:
    raw = (icon or '').strip().lower().replace('_', '-')
    if raw in ICON_ALLOWLIST:
        return raw
    if category == 'imperfection' or severity in ('caution', 'issue'):
        return 'exclamation-triangle'
    if category == 'feature' or severity == 'positive':
        return 'stars'
    return 'info-circle'


def _normalize_grok_highlights(payload: dict, max_highlights: int) -> dict:
    scene = str(payload.get('scene') or 'other').strip().lower()
    if scene not in SCENE_VALUES:
        # fuzzy map
        if 'dash' in scene:
            scene = 'interior_dash'
        elif 'cabin' in scene or 'interior' in scene:
            scene = 'interior_cabin'
        elif 'wheel' in scene:
            scene = 'wheel_closeup'
        elif 'front' in scene:
            scene = 'exterior_front'
        elif 'rear' in scene:
            scene = 'exterior_rear'
        elif 'side' in scene or 'exterior' in scene:
            scene = 'exterior_side'
        else:
            scene = 'other'

    raw_items = payload.get('highlights') or payload.get('hotspots') or []
    if not isinstance(raw_items, list):
        raw_items = []

    candidates: List[HighlightCandidate] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        try:
            x = float(item.get('x_pct', item.get('x', 50)))
            y = float(item.get('y_pct', item.get('y', 50)))
        except (TypeError, ValueError):
            continue
        # Accept 0-1 normalized coords
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and (x <= 1.0 and y <= 1.0):
            # Heuristic: if both look like fractions and max<=1, scale
            if x <= 1.0 and y <= 1.0 and (item.get('x_pct') is None and item.get('y_pct') is None):
                x, y = x * 100.0, y * 100.0
            elif max(x, y) <= 1.5:
                x, y = x * 100.0, y * 100.0

        label = str(item.get('label') or item.get('title') or 'Detail').strip()[:120]
        if not label:
            continue
        category = str(item.get('category') or 'detail').strip().lower()
        if category not in ('feature', 'imperfection', 'detail'):
            if category in ('condition', 'damage', 'defect'):
                category = 'imperfection'
            elif category in ('option', 'equipment'):
                category = 'feature'
            else:
                category = 'detail'
        severity = str(item.get('severity') or 'info').strip().lower()
        if severity not in ('positive', 'info', 'caution', 'issue'):
            severity = 'caution' if category == 'imperfection' else ('positive' if category == 'feature' else 'info')
        try:
            conf = float(item.get('confidence', 0.7))
        except (TypeError, ValueError):
            conf = 0.7
        # Grok should already be conservative; still floor weak guesses
        if conf < 0.55:
            continue
        desc = str(item.get('description') or item.get('text') or '').strip()
        desc = _shopper_description(label, category, desc)
        icon = _normalize_icon(item.get('icon'), category, severity)
        candidates.append(HighlightCandidate(
            x_pct=x,
            y_pct=y,
            label=label,
            category=category,
            description=desc[:500],
            icon=icon,
            severity=severity,
            confidence=conf,
            order_index=idx,
            meta={'engine': 'grok'},
        ))

    final = _nms_highlights(
        candidates,
        min_dist_pct=12.0,
        max_items=max(1, min(16, int(max_highlights or 12))),
        min_confidence=float(os.environ.get('PHOTO_HIGHLIGHTS_MIN_CONF', '0.55')),
    )
    return {
        'scene': scene,
        'highlights': [h.to_dict() for h in final],
        'analysis_version': ANALYSIS_VERSION,
        'engine': 'grok',
    }


_GENERIC_DESC = re.compile(
    r'\b('
    r'listed on this vehicle|listed for this vehicle|is listed|are listed|'
    r'highlighted for closer inspection|highlighted for inspection|'
    r'visible or listed|nice feature|great feature'
    r')\b',
    re.I,
)


def _shopper_description(label: str, category: str, desc: str) -> str:
    """Replace empty or boilerplate notes with something a shopper can use."""
    text = (desc or '').strip()
    if text and not _GENERIC_DESC.search(text) and len(text) >= 40:
        return text
    catalog = match_feature_catalog(label)
    if catalog:
        return catalog[0][1]['description']
    name = label.strip() or 'This'
    if category == 'imperfection':
        return (
            f'{name} is visible in this photo. Small marks like this are common and usually cosmetic — '
            'confirm the size in person and ask whether it is paint-only or into the panel.'
        )
    if category == 'feature':
        return (
            f'{name} is on this vehicle. Ask how it works on a test drive and confirm the control '
            'responds before you count on it.'
        )
    return (
        f'{name} is called out so you can inspect it up close. Look for wear, cracks, or anything '
        'that would change how you use it day to day.'
    )


def _build_grok_prompt(
    features_text: Optional[str],
    vehicle_context: Optional[dict],
    max_highlights: int,
) -> Tuple[str, str]:
    ctx = vehicle_context or {}
    vehicle_bits = []
    for key in ('year', 'make', 'model', 'body_style', 'exterior_color', 'interior_color', 'drivetrain', 'transmission'):
        val = ctx.get(key)
        if val not in (None, ''):
            vehicle_bits.append(f'{key}={val}')
    vehicle_line = ', '.join(vehicle_bits) if vehicle_bits else 'unknown vehicle'
    features_line = (features_text or '').strip() or '(none provided)'
    matched = match_feature_catalog(features_text)
    matched_labels = ', '.join(m['label'] for _, m in matched) or '(none matched)'

    cap = max(1, min(16, int(max_highlights or 12)))
    system = (
        'You write thorough Carvana-style photo hotspots for a used-car shopper. '
        'Mark every distinct, visible feature in this photo — be complete, not sparse. '
        'Each marker must sit on the part itself and tell the customer what it does for them, '
        'one practical detail, and a short confirm-in-person note only if needed. '
        'Use the listing features as a checklist: if that equipment is visible here, it must get a marker. '
        'Also mark visible equipment that is not on the list. '
        'Never invent options, mileage, prices, history, or damage. '
        'Never place markers on sky, driveway, buildings, people, plates, or empty background. '
        'Coordinates are percentages of the FULL image width/height (0-100), origin top-left. '
        'Respond with JSON only — no markdown, no commentary.'
    )

    user = f"""Analyze this dealership vehicle photo and return JSON only.

Vehicle: {vehicle_line}
Listed features text: {features_line}
Listing features to mark when visible in THIS photo: {matched_labels}

Be thorough. Mark each distinct visible item once, up to {cap}. Spread markers so they do not stack.

Exterior front: headlights (LED/HID/halogen/projector), daytime running lights, fog lights, grille and badge, hood scoop, bug shield, front camera, parking sensors, tow hooks, skid plate, winch, light bar.
Exterior side: wheels and tire type, running boards or steps, roof rails or crossbars, window visors, tint, mirrors, door handles only if keyless/sensors are visible, fender flares, mud flaps, side steps, charge port.
Exterior rear: tail lights, backup camera, hitch and wiring plug, tow hooks, bed liner, tonneau, toolbox, spoiler, power liftgate or tailgate, trailer plug, spare if visible.
Wheel closeup: wheel finish, tire tread condition, tire type (all-terrain, new), curb rash.
Interior dash: touchscreen, CarPlay/Android Auto area, digital cluster, steering-wheel controls, heated wheel, push-button start, drive modes, head-up display, climate controls, wireless charger, HomeLink.
Interior cabin: leather or cloth, heated/ventilated seats, captain chairs, third row, sunroof or panoramic glass, ambient light, premium audio speakers, floor mats, fold-flat seats, car-seat anchors.
Engine bay (scene=other): engine cover, turbo or supercharger if visible, battery, obvious leaks only when clearly wet.

Also mark one clear scratch, scuff, ding, chip, or curb rash when you can see it. If unsure, omit it. Max 2 imperfections.

Do not mark plain paint, empty bumper skin, or the sky. Do not put cabin features on an exterior photo.
If the photo is too dark, blurry, or shows almost no vehicle, return an empty highlights list.

Rules:
1. scene must be one of: exterior_front, exterior_side, exterior_rear, wheel_closeup, interior_dash, interior_cabin, other
2. Return up to {cap} highlights. Include every clearly visible feature, not just the best 2.
3. Each highlight needs: label, category (feature|imperfection|detail), severity (positive|info|caution|issue), x_pct, y_pct, confidence (0-1), description, icon (one of: phone, stars, thermometer-half, wind, brightness-high, camera-video, eye, signpost, speedometer, geo-alt, bluetooth, key, truck, distribute-horizontal, circle, check-circle, shuffle, speaker, people, door-open, power, exclamation-triangle, exclamation-circle, info-circle, display, bullseye, cloud-rain, window, shield, moon, wifi, broadcast, disc, arrows-expand, snow, house, mic, gear, fuel-pump, battery-charging, arrow-up, arrow-down, pause).
4. x_pct/y_pct must sit ON the object. Not empty space.
5. label is short and specific ("Apple CarPlay", "Trailer Hitch", "LED Headlights") — not "Feature" or "Detail".
6. description is 1-2 sentences a shopper can use. Say what it does for them and one practical check. Do not say "listed on this vehicle", "highlighted for inspection", or "nice feature". Do not invent specs you cannot see.
7. confidence >= 0.65 for anything you include.

Good description: "Alloy wheels with some light curb rash on the outer lip — cosmetic only, and cheaper than a tire replacement. Look for a bend or a sidewall bubble before you worry about the rash."
Bad description: "Alloy wheels are listed on this vehicle."

JSON schema:
{{
  "scene": "exterior_side",
  "highlights": [
    {{
      "label": "Alloy Wheels",
      "category": "feature",
      "severity": "positive",
      "x_pct": 22.5,
      "y_pct": 68.0,
      "confidence": 0.86,
      "description": "Factory-style alloy wheel. Light curb rash on the lip is cosmetic; a bend or a bubble in the sidewall is what to flag in person.",
      "icon": "circle"
    }}
  ]
}}
"""
    return system, user


def analyze_with_grok(
    image_path: str,
    features_text: Optional[str] = None,
    vehicle_context: Optional[dict] = None,
    max_highlights: int = 12,
) -> dict:
    api_key = _xai_api_key()
    if not api_key:
        raise RuntimeError('XAI_API_KEY is not set')

    data_url = _image_to_data_url(image_path)
    system, user = _build_grok_prompt(features_text, vehicle_context, max_highlights)
    model = _grok_model()
    timeout = float(os.environ.get('PHOTO_HIGHLIGHTS_GROK_TIMEOUT', '90'))

    body = {
        'model': model,
        'temperature': 0.1,
        'max_tokens': 2800,
        'messages': [
            {'role': 'system', 'content': system},
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'image_url',
                        'image_url': {
                            'url': data_url,
                            'detail': os.environ.get('PHOTO_HIGHLIGHTS_GROK_DETAIL', 'high'),
                        },
                    },
                    {'type': 'text', 'text': user},
                ],
            },
        ],
    }

    req = urllib.request.Request(
        'https://api.x.ai/v1/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'marshallauto-highlights/3',
        },
        method='POST',
    )
    # Prefer certifi CA bundle (macOS python.org builds often lack system certs)
    ssl_context = None
    try:
        import ssl
        import certifi
        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ssl_context = None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        err_body = ''
        try:
            err_body = exc.read().decode('utf-8', errors='replace')[:500]
        except Exception:
            pass
        raise RuntimeError(f'xAI HTTP {exc.code}: {err_body or exc.reason}') from exc
    except Exception as exc:
        raise RuntimeError(f'xAI request failed: {exc}') from exc

    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise RuntimeError('Invalid JSON from xAI') from exc

    content = ''
    try:
        content = payload['choices'][0]['message']['content'] or ''
    except Exception as exc:
        raise RuntimeError(f'Unexpected xAI response shape: {str(payload)[:300]}') from exc

    if isinstance(content, list):
        # Some SDKs return content parts
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get('type') in ('text', 'output_text'):
                parts.append(part.get('text') or '')
            elif isinstance(part, str):
                parts.append(part)
        content = '\n'.join(parts)

    parsed = _extract_json_object(str(content))
    if not parsed:
        raise RuntimeError(f'Could not parse Grok JSON: {str(content)[:400]}')

    return _normalize_grok_highlights(parsed, max_highlights)


def analyze_with_opencv(
    image_path: str,
    features_text: Optional[str] = None,
    vehicle_context: Optional[dict] = None,
    max_highlights: int = 12,
) -> dict:
    """Conservative OpenCV fallback when Grok is unavailable."""
    cv2, np = _import_cv2()
    if not image_path or not os.path.isfile(image_path):
        raise FileNotFoundError(f'Image not found: {image_path}')

    bgr = cv2.imread(image_path)
    if bgr is None:
        raise ValueError(f'Could not decode image: {image_path}')

    bgr, _scale = _resize_for_analysis(cv2, np, bgr)
    h, w = bgr.shape[:2]
    scene = classify_scene(cv2, np, bgr)
    wheels = _detect_wheels(cv2, np, bgr)
    screen_pt = _detect_screen_region(cv2, np, bgr)
    seat_pts = _detect_seat_regions(cv2, np, bgr) if scene.startswith('interior') else []
    matched = match_feature_catalog(features_text)

    ctx = vehicle_context or {}
    extra_bits = []
    for key in ('drivetrain', 'transmission', 'body_style'):
        val = ctx.get(key)
        if val:
            extra_bits.append(str(val))
    if extra_bits:
        matched_extra = match_feature_catalog(','.join(extra_bits))
        seen = {m['label'] for _, m in matched}
        for item in matched_extra:
            if item[1]['label'] not in seen:
                matched.append(item)
                seen.add(item[1]['label'])

    candidates: List[HighlightCandidate] = []
    if scene.startswith('exterior') or scene == 'wheel_closeup':
        candidates.extend(_wheel_highlights(cv2, np, bgr, wheels))
    candidates.extend(_detect_imperfections(cv2, np, bgr, scene))
    candidates.extend(_feature_placements(scene, matched, screen_pt, seat_pts, wheels, w, h))

    if screen_pt and scene.startswith('interior') and float(screen_pt[2]) >= 0.6:
        if not any('carplay' in (c.label or '').lower() or 'android' in (c.label or '').lower()
                   or 'navigation' in (c.label or '').lower() for c in candidates):
            meta = IMPERFECTION_META['infotainment']
            candidates.append(HighlightCandidate(
                x_pct=screen_pt[0],
                y_pct=screen_pt[1],
                label=meta['label'],
                category=meta['category'],
                description=meta['description'],
                icon=meta['icon'],
                severity=meta['severity'],
                confidence=float(screen_pt[2]),
                order_index=5,
            ))

    max_items = max(1, min(16, int(max_highlights or 12)))
    final = _nms_highlights(
        candidates,
        min_dist_pct=12.0,
        max_items=max_items,
        min_confidence=float(os.environ.get('PHOTO_HIGHLIGHTS_MIN_CONF', '0.55')),
    )
    return {
        'scene': scene,
        'highlights': [h.to_dict() for h in final],
        'analysis_version': ANALYSIS_VERSION,
        'engine': 'opencv',
    }


def analyze_vehicle_image(
    image_path: str,
    features_text: Optional[str] = None,
    vehicle_context: Optional[dict] = None,
    max_highlights: int = 12,
) -> dict:
    """
    Analyze a vehicle photo and return highlight candidates.

    Returns:
        {
          'scene': str,
          'highlights': [HighlightCandidate.to_dict(), ...],
          'analysis_version': int,
          'engine': str,
        }
    """
    if not image_path or not os.path.isfile(image_path):
        raise FileNotFoundError(f'Image not found: {image_path}')

    engine_pref = _engine_preference()
    max_highlights = int(max_highlights or os.environ.get('PHOTO_HIGHLIGHTS_MAX', '12') or 12)
    max_highlights = max(1, min(16, max_highlights))

    want_grok = engine_pref in ('grok', 'xai', 'auto', '')
    force_opencv = engine_pref in ('opencv', 'local', 'cv')

    if want_grok and not force_opencv and _xai_api_key():
        try:
            return analyze_with_grok(
                image_path,
                features_text=features_text,
                vehicle_context=vehicle_context,
                max_highlights=max_highlights,
            )
        except Exception as exc:
            logger.warning('Grok highlight analysis failed, falling back to OpenCV: %s', exc)
            if engine_pref in ('grok', 'xai') and os.environ.get(
                'PHOTO_HIGHLIGHTS_GROK_REQUIRED', ''
            ).lower() in ('1', 'true', 'yes', 'on'):
                raise

    return analyze_with_opencv(
        image_path,
        features_text=features_text,
        vehicle_context=vehicle_context,
        max_highlights=max_highlights,
    )
