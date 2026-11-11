"""Initial buyer guides, published explicitly and editable through the blog admin."""

from datetime import datetime, timezone

import click
from flask import current_app

from app import db
from app.models import BlogPost, SiteSetting


BUYER_GUIDES = (
    {
        'slug': 'rebuilt-vs-salvage-titles-north-carolina',
        'title': 'Rebuilt vs. Salvage Titles in North Carolina: A Buyer Guide',
        'excerpt': 'Understand title brands, the limits of title inspections, and the documents to request before buying a rebuilt-title vehicle in North Carolina.',
        'content': """
<p>A lower asking price can make a rebuilt-title vehicle worth researching, but
price alone does not tell you what happened to the car or how it was repaired.
Start with the actual title, the vehicle identification number (VIN), and the
available repair records rather than relying on a listing label.</p>
<h2>What is the difference between salvage and rebuilt?</h2>
<p>North Carolina law recognizes both salvage motor vehicles and salvage rebuilt
vehicles as title classifications. In general buyer terminology, salvage points
to a vehicle's damage or loss history, while rebuilt describes a vehicle repaired
after salvage. Those words are not interchangeable with a clean title, and an
online description is not proof that a particular vehicle can be registered.
Ask to see the exact brand on the title and confirm the vehicle's current
titling and registration requirements with NCDMV.</p>
<p>Under North Carolina's title-branding statute, once the Division issues a
branded title, subsequent titles must continue to reflect that branding. The
statute also addresses vehicles branded in another state. Moving a vehicle
between states is not a reason to disregard its earlier history.</p>
<h2>A title inspection is not a safety guarantee</h2>
<p>This distinction matters: the preliminary and final inspections described
in G.S. 20-71.3(b) are anti-theft measures. The statute explicitly says they do
not certify safety or roadworthiness. Do not treat a rebuilt title, a state
inspection document, or a history report as a substitute for an independent
mechanical and structural assessment.</p>
<h2>Documents to request before deciding</h2>
<ul>
<li>The title and VIN, with any brands or liens explained.</li>
<li>Available before-repair photographs, repair invoices, and parts records.</li>
<li>Available service and vehicle-history reports, including gaps in the record.</li>
<li>Any inspection documents, with an explanation of what each inspection covered.</li>
<li>Written sale terms, including whether a warranty is provided.</li>
</ul>
<p>Ask an independent qualified professional to assess the repairs and vehicle
condition. Missing documentation is something to investigate, not a reason to
assume repairs were either good or bad.</p>
<h2>Compare the whole ownership decision</h2>
<p>Get an insurance quote using the VIN and disclosed title brand before buying.
If you need a loan, ask the lender whether it accepts that specific vehicle.
Compare the purchase price, available coverage, loan terms, expected repairs,
and the effect the title history may have when you sell later.</p>
<p><a href="/inventory?title_status=rebuilt">Browse current rebuilt-title listings</a>,
then <a href="/contact">ask about the records for a specific vehicle</a>.
For the next steps, read our
<a href="/blog/financing-rebuilt-title-car">rebuilt-title financing guide</a>
and <a href="/blog/used-car-inspection-checklist">used-car buying checklist</a>.</p>
<h2>Official source</h2>
<p><a href="https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_20/GS_20-71.3.html">North Carolina General Statutes, G.S. 20-71.3</a>
sets out title branding and the limits of the specified inspections. Requirements
depend on the vehicle and circumstances; confirm current requirements with NCDMV.</p>
""",
    },
    {
        'slug': 'financing-rebuilt-title-car',
        'title': 'Can You Finance a Rebuilt-Title Car? Questions to Ask',
        'excerpt': 'Ask about lender eligibility, insurance, down payments, APR, and total borrowing costs before committing to a rebuilt-title car.',
        'content': """
<p>Financing a rebuilt-title vehicle may be possible, but approval is not
automatic. A lender must be willing to finance the particular vehicle as well
as approve the borrower. Before paying a deposit or planning your budget around
a monthly payment, disclose the title brand and provide the VIN.</p>
<h2>Start with vehicle eligibility</h2>
<p>Ask the lender directly: do you finance this title classification, and does
this VIN qualify? Ask whether there are restrictions based on age, mileage,
repair documentation, or vehicle value. A general preapproval does not
necessarily mean that every car you find will qualify. Get any vehicle-specific
conditions explained before you commit.</p>
<p>If a lender will not finance the vehicle, compare other legitimate options
without assuming another lender will accept it. Avoid applications based on an
incomplete or inaccurate description of the title history.</p>
<h2>Check insurance before closing the loan</h2>
<p>Give your insurer the VIN and title brand and ask for a written quote. Ask
what coverage is available, what exclusions apply, and how a future claim would
be valued. If the lender requires particular coverage, confirm that the policy
you can obtain satisfies those requirements. An affordable loan payment does
not help if the required insurance is unavailable or outside your budget.</p>
<h2>Compare more than the monthly payment</h2>
<p>The Consumer Financial Protection Bureau encourages shoppers to compare
financing options and understand the terms before signing. For each offer,
write down:</p>
<ul>
<li>The cash price and itemized out-the-door price.</li>
<li>Your down payment and the amount financed.</li>
<li>The annual percentage rate (APR), loan term, and payment schedule.</li>
<li>The finance charge and total of payments shown in the disclosures.</li>
<li>Any optional products, fees, or conditions attached to the offer.</li>
</ul>
<p>A longer term may reduce the monthly payment while increasing total borrowing
costs. Include insurance, routine maintenance, and a repair reserve in your
budget. Ask for an explanation of unfamiliar charges rather than evaluating
the deal using the payment alone.</p>
<h2>Before you sign</h2>
<p>Check that the contract matches the vehicle, title disclosures, and terms
you agreed to. Do not sign blank or partially completed forms. Confirm whether
financing is final, retain copies of every document, and understand when and
where payments are due. These are important checks for any used-car loan,
not just one involving a rebuilt title.</p>
<p><a href="/financing">Contact us about financing</a> with the stock number
or VIN of a vehicle in our <a href="/inventory">current inventory</a>.
Financing, rates, down payments, and insurance depend on the applicant and
vehicle; this guide is not an offer or promise of approval. Also read
<a href="/blog/rebuilt-vs-salvage-titles-north-carolina">our NC title guide</a>
before choosing a rebuilt-title car.</p>
<h2>Official sources</h2>
<ul>
<li><a href="https://www.consumerfinance.gov/consumer-tools/auto-loans/">CFPB: Auto loan shopping and comparison tools</a></li>
<li><a href="https://www.consumerfinance.gov/ask-cfpb/what-should-i-know-before-i-finalize-a-car-or-auto-loan-en-2131/">CFPB: Before finalizing a car or auto loan</a></li>
</ul>
""",
    },
    {
        'slug': 'used-car-inspection-checklist',
        'title': 'What to Check Before Buying a Used Car: A Practical Checklist',
        'excerpt': 'Use this checklist to compare vehicle records, arrange an independent inspection, review title history, and understand the final purchase terms.',
        'content': """
<p>A useful used-car checklist starts before the test drive and ends with the
paperwork. It should help you identify questions for the seller and an
independent professional, not encourage you to diagnose unfamiliar mechanical
or safety systems yourself. Set a budget that includes insurance, maintenance,
and potential repairs as well as the purchase price.</p>
<h2>1. Confirm which vehicle you are researching</h2>
<p>Save the listing, stock number, and VIN. Compare the year, make, model,
trim, mileage, asking price, and title status with the records you receive.
Ask which advertised features have been verified on this vehicle; equipment
lists and VIN decoders do not always establish its current condition.</p>
<p>Before traveling to see it, contact the seller to confirm availability.
Ask about known imperfections and whether relevant documents will be available
at your visit.</p>
<h2>2. Review the title and history</h2>
<p>Ask to see the title information, any disclosed brands, and any lien or
payoff requirements. Review available service records and vehicle-history
reports. Ask about accidents, repairs, inconsistent mileage entries, and gaps
in the record. A report can be useful without being a complete account of a
vehicle's life.</p>
<p>For branded vehicles, request available repair invoices and before-repair
photos. Our <a href="/blog/rebuilt-vs-salvage-titles-north-carolina">North Carolina
salvage and rebuilt-title guide</a> explains why title-related inspections
should not be treated as a safety certification.</p>
<h2>3. Arrange an independent inspection</h2>
<p>Ask whether an independent qualified mechanic can inspect the vehicle before
you commit. Discuss the title history and your intended use with the inspector.
Request a written assessment of condition, needed work, and estimated costs.
A test drive and history report are not substitutes for this assessment.</p>
<p>Do not perform unfamiliar repairs or inspections yourself. If there is a
possible safety issue, have a qualified professional evaluate it before
driving or purchasing the vehicle.</p>
<h2>4. Make the visit count</h2>
<p>Check practical fit: seating, cargo space, visibility, and the features you
need. Ask the seller to explain warning lights, condition notes, and any
equipment that does not work. Discuss a test drive with the seller and follow
their requirements. Write down unresolved questions rather than assuming an
issue is minor or that a verbal promise will be included in the sale.</p>
<h2>5. Review the total price and written terms</h2>
<p>Request an itemized out-the-door price. Understand taxes, fees, optional
products, warranty terms, and whether the vehicle is sold as-is. Get important
promises in writing. If financing, compare the APR, term, amount financed,
and total payments, and confirm insurance for the exact VIN.</p>
<p>Before signing, check every completed document and keep copies. The CFPB
advises against signing blank or partially completed forms. Pause if the
paperwork does not match the agreement you understood.</p>
<p><a href="/inventory">Browse current vehicles</a> or
<a href="/contact">contact us with your vehicle questions</a>. If you need
financing for a branded vehicle, use our
<a href="/blog/financing-rebuilt-title-car">rebuilt-title loan checklist</a>
to prepare your questions.</p>
<h2>Official sources</h2>
<ul>
<li><a href="https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_20/GS_20-71.3.html">NC G.S. 20-71.3: Title brands and inspection limitations</a></li>
<li><a href="https://www.consumerfinance.gov/ask-cfpb/what-should-i-know-before-i-finalize-a-car-or-auto-loan-en-2131/">CFPB: Finalizing the purchase and loan paperwork</a></li>
</ul>
""",
    },
    {
        'slug': 'buying-a-used-car-in-winter-checklist',
        'title': 'Buying a Used Car in Winter: What to Check When Days Are Short',
        'excerpt': 'Shorter days and cold mornings change how you should shop. Plan a daylight visit, ask for a cold start, and check the battery, tires, heat, lights, and recalls.',
        'content': """
<p>Early sunsets and cold mornings are a good reason to adjust how you shop for a
used car. Poor light can hide paint and body problems, and cold weather exposes
weak batteries, worn tires, and heating problems that a warm afternoon test drive
might not. This checklist helps you plan the visit and ask better questions. It
does not replace an inspection by an independent qualified mechanic.</p>
<h2>Plan the visit around daylight</h2>
<p>Look at the vehicle outside in good daylight, because dim or wet conditions
can hide dents, mismatched paint, scratches, and uneven panel gaps. Seasonal
closing times can also change when the days get shorter, so
<a href="/contact">confirm current hours and that the vehicle is still available</a>
before you travel, and aim to arrive early enough to finish the inspection and the
test drive before dark. Bring a flashlight to look at wheel wells, the engine
bay, and the interior footwells.</p>
<h2>Ask for a cold start</h2>
<p>Ask the seller not to warm the vehicle up before you arrive. A cold start is
when many problems show themselves: slow or hard starting, rough idling,
unusual noises, smoke from the exhaust, or warning lights that stay on after
the engine starts. Write down anything you notice and ask about it instead of
assuming it is minor.</p>
<h2>Battery and charging system</h2>
<p>Cold weather makes a weak battery much easier to expose. Ask how old the
battery is and whether it has been tested. A parts store or independent shop can
load-test it and check the charging system, which is usually quick and
inexpensive compared with being stranded.</p>
<h2>Tires and brakes</h2>
<p>Ask an independent professional to measure tread depth and look for uneven
wear, which can point to alignment or suspension issues. Tires also age: the
four-digit DOT date code on the sidewall shows the week and year of manufacture,
and old rubber can harden and lose grip even when the tread looks fine. If the
tires will need replacing soon, include that in your budget and your
negotiation. Pay attention to braking feel, noises, and pulling during the test
drive.</p>
<h2>Heat, defrost, wipers, and lights</h2>
<p>Do not wait until the first frosty morning to find out these do not work.
During your visit, check that:</p>
<ul>
<li>The heater warms up and the front and rear defrosters clear the glass.</li>
<li>The wipers clear the windshield without streaking, and the washer sprays.</li>
<li>Headlights, high beams, brake lights, turn signals, and fog lights all work.</li>
<li>The seat heaters, mirrors, and window and door locks operate as expected.</li>
</ul>
<h2>Leaks, rust, and road salt</h2>
<p>Look under the vehicle for fresh fluid on the ground. If the vehicle spent
time in a state that salts its roads, ask an independent mechanic to check the
underbody, brake lines, and suspension for corrosion that is hard to see from
the outside.</p>
<h2>Check for open recalls by VIN</h2>
<p>Save the VIN from the listing and search it on
<a href="https://www.nhtsa.gov/recalls">NHTSA's recall lookup</a>. The tool shows
unrepaired safety recalls for that VIN, though it will not show recalls that
have already been repaired or every manufacturer campaign. Ask the seller how
any open recall will be handled.</p>
<h2>Review the whole deal</h2>
<p>Compare the title status, vehicle history, and written terms before you
commit. Our <a href="/blog/used-car-inspection-checklist">used-car checklist</a>
covers the records and paperwork, and our
<a href="/blog/rebuilt-vs-salvage-titles-north-carolina">title guide</a> explains
what to ask about branded vehicles. Ready to look?
<a href="/inventory">Browse our current used cars</a> or
<a href="/contact">contact us with your questions</a>.</p>
""",
    },
    {
        'slug': 'used-car-mileage-guide',
        'title': 'Used-Car Mileage: What Is Too High, and What Should You Check?',
        'excerpt': 'There is no mileage number that makes every used car a good or bad buy. Learn how to weigh odometer readings, maintenance records, age, condition, and price.',
        'content': """
<p>“How many miles is too many?” is one of the first questions shoppers ask
about a used car. It is also the wrong question to answer with a single cutoff.
Odometer mileage is useful context, but it cannot tell you on its own how a
vehicle was maintained or what it will need next.</p>
<h2>Read the miles alongside the car's age</h2>
<p>Compare the odometer with the model year and the vehicle's dated service,
inspection, and title records. A mileage history that rises steadily is easier
to understand than one with a long gap or a sudden change. If an entry does not
make sense, ask the seller to explain it and have the records reviewed before
you decide. A history report can help, but it may not include every service
visit or event.</p>
<p>How a car was used matters, too, but do not assume that highway miles are
automatically harmless or that city miles mean a car is worn out. Ask about
maintenance and have the vehicle's current condition assessed. Evidence is more
useful than a guess based on the previous owner's commute.</p>
<h2>Look for a maintenance story, not just a low number</h2>
<p>Ask for available service receipts and check them against the maintenance
schedule for that exact year, make, and model. Pay attention to overdue or
poorly documented maintenance that could affect major systems. An independent
mechanic can assess wear on the engine, transmission, suspension, tires, and
brakes and explain which repairs are due now versus later. That written
assessment gives you a better basis for budgeting than the odometer alone.</p>
<h2>Consider the whole cost of ownership</h2>
<p>A lower-mileage car is not automatically the better value if its price is
higher or its condition and records raise unanswered questions. Compare the
purchase price, inspection findings, likely maintenance, insurance quote, and
fuel costs. For fuel economy estimates, search the specific year, make, and
model on <a href="https://www.fueleconomy.gov/">FuelEconomy.gov</a>; your actual
results will vary with driving, maintenance, weather, and vehicle condition.</p>
<p>Before you visit, save the listing and VIN, confirm the vehicle is available,
and ask whether you can arrange an independent inspection. Our
<a href="/blog/used-car-inspection-checklist">used-car inspection checklist</a>
covers records and the final paperwork. If the vehicle has a branded title,
read our <a href="/blog/rebuilt-vs-salvage-titles-north-carolina">North Carolina
salvage and rebuilt-title guide</a>, too. You can
<a href="/inventory">compare vehicles in our current inventory</a> or
<a href="/contact">contact us with a question about a specific car</a>.</p>
<h2>One more VIN check</h2>
<p>Before buying, check the VIN for unrepaired safety recalls using
<a href="https://www.nhtsa.gov/recalls">NHTSA's recall lookup</a>. A recall
search does not replace a mechanical inspection, and it may not show every
manufacturer campaign. Mileage, records, and a professional assessment each
answer different questions; use all three rather than relying on any one of
them.</p>
""",
    },
    {
        'slug': 'how-to-compare-used-cars',
        'title': 'How to Compare Used Cars: A Side-by-Side Buyer Checklist',
        'excerpt': 'Comparing used cars is easier when you weigh the same details for each one. Use this practical checklist for condition, history, price, and everyday fit.',
        'content': """
<p>When two used cars both look promising, it is easy to fixate on the asking
price or whichever one you saw most recently. A simple side-by-side comparison
helps you slow down and notice the differences that matter after the first
week of ownership. Start with how you will use the car, then compare the same
details for every candidate.</p>
<h2>Write down what your day-to-day driving requires</h2>
<p>Think about the routes you actually drive, how many people usually ride
along, and what you need to carry. A small car may be easier to park and use
less fuel; a larger vehicle may suit your passenger or cargo needs better.
There is no universal best choice. Make a short list of must-haves, nice-to-
haves, and deal-breakers before the features in a listing make the decision
for you.</p>
<h2>Compare like with like</h2>
<p>Record the year, make, model, trim, mileage, title status, advertised price,
and VIN for each vehicle. Similar model names can have different equipment,
engines, and safety features, so verify what is actually on the car instead of
assuming every trim is the same. Check the VIN against the listing and the
documents you receive.</p>
<p>Then compare what you can verify: maintenance records, available history,
disclosed condition, and the findings from an independent inspection. Ask the
same questions about each vehicle. If one has an unanswered history gap or
needs work, note it rather than treating an incomplete record as proof of
either good or bad condition. Our
<a href="/blog/used-car-mileage-guide">guide to used-car mileage</a> explains
why the odometer should be read alongside a vehicle's age and maintenance.</p>
<h2>Compare the cost you will actually pay</h2>
<p>Ask for an itemized out-the-door price for each car. Add a quote for
insurance using the VIN, likely maintenance identified by an inspector, and
fuel costs based on the way you drive. The EPA's
<a href="https://www.fueleconomy.gov/">FuelEconomy.gov</a> offers estimates
that can help you compare models, but real-world mileage varies.</p>
<p>If you plan to finance, compare the APR, loan term, amount financed, and
total of payments, not just the monthly amount. The
<a href="https://www.consumerfinance.gov/consumer-tools/auto-loans/">CFPB's
auto-loan tools</a> can help you understand and compare financing offers.
Approval and terms depend on the lender, borrower, and vehicle.</p>
<h2>Give condition and paperwork equal weight</h2>
<p>Arrange an independent inspection before committing, and ask the seller to
explain anything you do not understand in the report. Check the title and
written sale terms; an inspection, history report, or test drive is not a
substitute for the others. Our
<a href="/blog/used-car-inspection-checklist">practical inspection
checklist</a> walks through the records, visit, and paperwork. For branded
vehicles in North Carolina, see our
<a href="/blog/rebuilt-vs-salvage-titles-north-carolina">title guide</a>.</p>
<p>There may not be a perfect choice, but you can make a well-informed one.
Compare your notes, follow up on unanswered questions, and choose the car
whose condition and total cost fit your needs. Ready to start a shortlist?
<a href="/inventory">Browse our current vehicles</a> or
<a href="/contact">ask us about a specific listing</a>.</p>
<h2>Official buying guidance</h2>
<p>The Federal Trade Commission explains the
<a href="https://www.ftc.gov/business-guidance/resources/used-motor-vehicle-trade-regulation-rule">Used Car Rule and Buyers Guide</a>,
including the information dealers must provide to shoppers. Read the guide
provided with the vehicle and make sure any written warranty terms match what
you were told.</p>
""",
    },
)

# Guides published before per-guide tracking, covered by the original one-time marker.
_LEGACY_GUIDE_SLUGS = frozenset(guide['slug'] for guide in BUYER_GUIDES[:3])
_LEGACY_MARKER_KEY = 'buyer_guides_v1_published'


def published_buyer_guides() -> list[BlogPost]:
    return (
        BlogPost.query.filter(
            BlogPost.is_published.is_(True),
            BlogPost.slug.in_([guide['slug'] for guide in BUYER_GUIDES]),
        ).order_by(BlogPost.id).all()
    )


def register_commands(app):
    @app.cli.command('publish-buyer-guides')
    def publish_buyer_guides():
        """Publish each guide once, preserving subsequent admin decisions."""
        legacy_initialized = bool(SiteSetting.query.filter_by(key=_LEGACY_MARKER_KEY).first())
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        created = 0
        for guide in BUYER_GUIDES:
            slug = guide['slug']
            marker_key = f'buyer_guide_published:{slug}'
            if SiteSetting.query.filter_by(key=marker_key).first():
                continue
            if legacy_initialized and slug in _LEGACY_GUIDE_SLUGS:
                continue
            if BlogPost.query.filter_by(slug=slug).first():
                click.echo(f'Kept existing guide: {slug}')
            else:
                db.session.add(BlogPost(
                    **guide,
                    author_name=current_app.config['BUSINESS_NAME'],
                    seo_description=guide['excerpt'],
                    is_published=True,
                    created_at=now,
                    updated_at=now,
                    published_at=now,
                ))
                created += 1
            db.session.add(SiteSetting(key=marker_key, value='1'))
        db.session.commit()
        SiteSetting.invalidate_cache()
        click.echo(f'Published {created} buyer guide(s). Existing posts were not changed.')
