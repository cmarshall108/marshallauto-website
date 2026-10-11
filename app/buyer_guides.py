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
)


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
        """Initialize starter guides once, preserving subsequent admin decisions."""
        marker_key = 'buyer_guides_v1_published'
        if SiteSetting.query.filter_by(key=marker_key).first():
            click.echo('Published 0 buyer guide(s). Already initialized; admin changes were kept.')
            return
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        created = 0
        for guide in BUYER_GUIDES:
            if BlogPost.query.filter_by(slug=guide['slug']).first():
                click.echo(f"Kept existing guide: {guide['slug']}")
                continue
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
