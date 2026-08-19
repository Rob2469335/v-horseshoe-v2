"""Static RV domain knowledge — pure data, no behavior.

Keeping every table (type terms, new-price baselines, makes, red flags,
positive signals, brand weak spots, MPG ranges, motorhome models, life-ease
feature spec) in one data-only module means the analyzers stay reviewable and
domain logic can be unit-tested without touching parsers or the network.
"""

from __future__ import annotations

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PPL_BASE = "https://www.pplmotorhomes.com"
PPL_INVENTORY = (
    PPL_BASE
    + "/used-rvs-for-sale/all-available-vehicles?facetValueFilter=price%3a%5b0+TO+{budget}%5d"
)

# --------------------------------------------------------------------------
# RV type classification
# --------------------------------------------------------------------------
RV_TYPE_TERMS = {
    "Class A Motorhome": ["class a", "class-a", "diesel pusher", "gas class a"],
    "Class B Motorhome": ["class b", "class-b", "van camper", "camper van", "sprinter"],
    "Class C Motorhome": ["class c", "class-c", "mini motorhome", "mini-motorhome"],
    "Travel Trailer": [
        "travel trailer",
        "travel-trailer",
        "bumper pull",
        "camper trailer",
    ],
    "Fifth Wheel": ["fifth wheel", "fifth-wheel", "5th wheel"],
    "Toy Hauler": ["toy hauler", "toy-hauler"],
    "Truck Camper": ["truck camper", "truck-camper", "slide-in camper"],
    "Popup / Tent Camper": ["popup", "pop-up", "tent camper", "folding camper"],
}

# Approximate NEW price by type (USD) — basis for the depreciation curve.
BASE_NEW_PRICE = {
    "Class A Motorhome": 120000,
    "Class B Motorhome": 110000,
    "Class C Motorhome": 85000,
    "Travel Trailer": 38000,
    "Fifth Wheel": 55000,
    "Toy Hauler": 45000,
    "Truck Camper": 25000,
    "Popup / Tent Camper": 15000,
    "unknown": 45000,
}

RV_MAKES = [
    "airstream",
    "winnebago",
    "thor",
    "jayco",
    "forest river",
    "keystone",
    "grand design",
    "coachmen",
    "fleetwood",
    "gulf stream",
    "heartland",
    "dutchmen",
    "kz",
    "tiffin",
    "newmar",
    "roadtrek",
    "pleasure-way",
    "lazy daze",
    "classic",
    "northwood",
    "lance",
    "artic fox",
    "palomino",
    "rockwood",
    "freedom",
    "ace",
    "shasta",
    "starcraft",
    "crossroads",
    "camplite",
    "casita",
    "scamp",
    "oliver",
    "ethos",
    "aliner",
    "taxa",
]

# Condition red flags (critical) — hard penalties, verdict capped.
CRITICAL_RED_FLAGS = [
    "water damage",
    "water intrusion",
    "leak",
    "leaking",
    "roof leak",
    "mold",
    "mildew",
    "soft floor",
    "soft spot",
    "frame damage",
    "frame rot",
    "cracked frame",
    "flood",
    "fire damage",
    "salvage",
    "rebuilt title",
    "no title",
    "blown",
    "smoke damage",
    "deer hit",
    "collision damage",
    "inoperable",
    "does not run",
    "not running",
    "needs work",
    "as-is",
    "as is",
    "mice",
    "rodent",
    "wrecked",
    "totaled",
    "tow away",
    "junk",
]

# Scam / legitimacy signals for private-party listings (BBBs, RV Reports' 12
# private-seller scam patterns, and community threads). The 2026 research
# consensus: private-seller RV scams cluster on shipping/escrow/out-of-state
# stories, deposit pressure, email-only contact, "too good to be true" pricing,
# and a seller who refuses photos/inspection. Each entry is (regex, label).
# Detected signals CAP the deal score below "Good Deal" so a scammy bargain can
# never rank as an excellent deal.
SCAM_RISK_PATTERNS: list[tuple[str, str]] = [
    (
        r"\bship(?:ping)?\s+(?:anywhere|nationwide|worldwide|at\s+your\s+cost)|"
        r"\bshipped\s+(?:to\s+your\s+door|directly|asap)\b",
        "shipping arrangement pushed",
    ),
    (r"\bescrow|vehicle protection service", "escrow / protection-service pressure"),
    (
        r"\b(?:military|deployed|overseas|out\s*of\s*state|can't\s+meet|can\'t\s+meet)\b",
        "out-of-state / cannot-meet story",
    ),
    (
        r"\bdeposit\s+(?:to\s+hold|now|today)|(?:send|wire|pay)\s+(?:a|the)\s+deposit|"
        r"first\s+come\s+first\s+served|many\s+buyers\s+interested|act\s+(?:fast|now|quick)",
        "deposit / urgency pressure",
    ),
    (
        r"\bemail\s+only\b|\bvia\s+email\b|\btext\s+me\b|\b(?:zelle|venmo|cash\s*app|gift\s*card)\b",
        "payment / contact channel pressure",
    ),
    (
        r"\bbank\s*to\s*bank\b|\bwire\s+(?:transfer|funds)|moneygram|western\s+union|"
        r"\bno\s+(?:inspection|title)\b|title\s+available\s+upon|paypal\s+(?:friends|family)",
        "wire / non-reversible payment asked",
    ),
    (
        r"\b(?:my|the)\s+(?:relative|cousin|uncle|aunt|friend)\s+(?:is|owns|has|selling)|"
        r"\bselling\s+for\s+(?:a\s+)?(?:relative|friend|my\s+parent)",
        "selling-on-behalf claim",
    ),
    (
        r"\b(?:blown\s+(?:engine|motor)|needs\s+minor\s+work|engine\s+rebuilt)\b",
        "misleading-condition \u201cminor work\u201d claim",
    ),
]

# A price this far below the estimated fair value is either a genuine steal or a
# scam/parts-unit — neither can be ranked as a clean "Excellent Deal". Used by the
# deal score to add a verify-before-commit caveat instead of an unqualified bargain.
EXTREME_UNDERPRICE_RATIO = 0.6  # listed at <= 60% of estimated fair value


# Positive condition / feature signals.
POSITIVE_SIGNALS = [
    "no leaks",
    "clean title",
    "low miles",
    "new tires",
    "new roof",
    "new battery",
    "solar",
    "lithium",
    "inverter",
    "leveling jacks",
    "awning",
    "slide",
    "generator",
    "serviced",
    "well maintained",
    "one owner",
    "non-smoker",
    "nonsmoker",
    "no pets",
    "garage kept",
    "excellent condition",
    "good condition",
    "king bed",
    "dry bath",
    "queen bed",
    "bunks",
    "furnace",
    "ac",
    "air conditioning",
    "hot water",
    "newer",
    "upgraded",
    "recently",
]

# Known weak spots per RV make (community consensus). Each entry is
# (weak_spot, how_to_check_during_inspection).
KNOWN_WEAK_SPOTS: dict[str, list[tuple[str, str]]] = {
    "keystone": [
        (
            "Slide-out / window seal leaks",
            "Run a hose over the slide seals; check interior for water stains around the slide",
        ),
        (
            "Cheap exterior siding (delamination/peeling gel-coat)",
            "Look for bubbles or delamination on the sidewalls",
        ),
        (
            "Frame & suspension on heavy models",
            "Inspect the frame rails and axles for rust or sag",
        ),
    ],
    "grand design": [
        (
            "Smaller axles / bearing wear on some floorplans",
            "Check bearing condition and any grease around hubs",
        ),
        (
            "Rear-cap delamination on certain years",
            "Tap the rear cap for hollow/delaminated spots",
        ),
        (
            "Higher resale = often priced above 'low retail'",
            "Compare against national comps before offering",
        ),
    ],
    "forest river": [
        (
            "Roof & seam leaks if not maintained",
            "Inspect roof membrane, EPDM/TPO seams, and lap seals",
        ),
        (
            "Quality varies by sub-brand and plant",
            "Research the exact line (Rockwood/Palomino/etc.) on owner forums",
        ),
        (
            "Slide-seal issues on Rockwood lines",
            "Open/close slides and check for seal wear",
        ),
    ],
    "dutchmen": [
        (
            "Fit/finish quality-control complaints",
            "Check door alignment, cabinet gaps, and trim",
        ),
        (
            "Leak/delamination risk on older units",
            "Probe floors and walls for soft spots and water stains",
        ),
        (
            "Cheap entry materials; low resale",
            "Expect harder resale; negotiate accordingly",
        ),
    ],
    "heartland": [
        (
            "Slide-out seal issues on older models",
            "Exercise slides and check seals + interior water marks",
        ),
        (
            "Axle/bearing issues on heavy fifth wheels",
            "Have the running gear inspected before purchase",
        ),
    ],
    "winnebago": [
        (
            "Some 2010s Class C units had roof/seam issues",
            "Inspect roof and clearance lights for leaks",
        ),
        (
            "Ford E-series chassis needs transmission attention",
            "Check trans service records; test-drive up to temp",
        ),
    ],
    "airstream": [
        (
            "Leaks around windows/skylights if seals fail",
            "Check window gaskets and interior corners for staining",
        ),
        (
            "Axle/suspension age on older units",
            "Inspect axle condition; replacements are costly",
        ),
        ("High parts/maintenance cost", "Budget for specialty airstream-only parts"),
    ],
    "coachmen": [
        ("Fit/finish QC variance between units", "Inspect closely; every unit differs"),
        ("Leak risk if not maintained", "Check roof seals and windows"),
    ],
    "fleetwood": [
        (
            "Lap-seal/roof issues on older units",
            "Inspect the entire roof perimeter and seams",
        ),
    ],
    "country coach": [
        (
            "Expensive, aging parts (wiring/harness)",
            "Have an RV tech inspect the 12V/120V systems",
        ),
        ("Air-ride suspension maintenance", "Check air bags and compressor operation"),
    ],
    "tiffin": [
        (
            "Some slide mechanisms need attention",
            "Exercise slides fully; listen for grinding",
        ),
        ("Expensive parts", "Budget for premium maintenance"),
    ],
    "thor": [
        (
            "Quality varies by sub-brand",
            "Research the specific floorplan on owner forums",
        ),
        (
            "Cheap-ish interior materials",
            "Expect wear; inspect cabinets and upholstery",
        ),
    ],
    "jayco": [
        (
            "Frame/axle issues on some lighter trailers",
            "Inspect axles and wheel bearings",
        ),
        ("Seal maintenance critical", "Re-caulk roof/seams annually"),
    ],
    "kz": [
        (
            "Lesser insulation; budget build",
            "Check climate suitability; inspect build quality",
        ),
        ("Some build-quality complaints", "Probe for fit/finish issues"),
    ],
    "gulf stream": [
        ("Mid-tier QC; some complaints", "Inspect thoroughly unit by unit"),
    ],
    "palomino": [
        (
            "Soft-floor issues on older popups",
            "Probe floors thoroughly, especially near walls",
        ),
    ],
    "lance": [
        ("Rear-cap delamination on some models", "Tap the rear cap for hollow spots"),
    ],
    "roadtrek": [
        (
            "Electrical gremlins / cabinet cracking in some years",
            "Test all house systems; check for cracks",
        ),
    ],
}

MPG_BY_TYPE = {
    "Class A Motorhome": (6.0, 10.0, "diesel"),
    "Class B Motorhome": (14.0, 18.0, "gas"),
    "Class C Motorhome": (8.0, 14.0, "gas"),
    "Travel Trailer": (0.0, 0.0, "tow"),
    "Fifth Wheel": (0.0, 0.0, "tow"),
    "Toy Hauler": (0.0, 0.0, "tow"),
    "Truck Camper": (0.0, 0.0, "tow"),
    "Popup / Tent Camper": (0.0, 0.0, "tow"),
}

# Models we know are motorhomes even when the source never states the class.
KNOWN_MOTORHOME_MODELS = (
    "redhawk rize solis tellaro maestro traverse trend beyond revel daybreak outlook synergy "
    "sunseeker windsport vegas ace axis quattro siesta cambria prisma forester aurora sunrise "
    "view navion solera ekko roam boldt leprechaun galaxy greyhawk melbourne precept alante "
    "unity wonder joy outlaw delano sequence chateau horizon sunstar sportsmobile travalo "
    "cross trek four winds solara daybreak zion 190 210"
).split()

# Life-ease feature checklist for two people living in a Class B/C motorhome
# (from owner/industry consensus: lithium + solar + alternator for off-grid,
# 12V fridge/A/C, on-demand hot water, 4-season insulation, shower layout,
# walk-around bed, driving aids, levelers, generator).
#
# Pure data: matchers are described, evaluated by analysis.py.
#   attr          -> truthy string value on the listing's PPL attributes
#   attr_ge       -> (attr_name, min_int) integer attribute >= min
#   attr_contains -> (attr_name, substring) case-insensitive substring match
#   regex         -> case-insensitive regex over title + description + attrs
#   solar         -> consult the listing's computed solar analysis
#   low_miles     -> special: chassis miles < 60k for motorhomes, n/a for towables
LIFE_EASE_FEATURES = [
    {
        "key": "lithium",
        "label": "Lithium battery bank (LiFePO4)",
        "why": "Runs A/C, laptops, and the fridge without shore power or a noisy generator",
        "regex": r"lithium|lifepo4|li-?fe|agm",
    },
    {
        "key": "solar",
        "label": "Solar panels + MPPT charge controller",
        "why": "Keeps batteries topped up while parked — the single biggest quality-of-life upgrade",
        "solar": True,
        "regex": r"\bsolar\b",
    },
    {
        "key": "inverter",
        "label": "Pure-sine inverter",
        "why": "Lets you plug in normal 120V appliances and charge laptops off the battery",
        "regex": r"\binverter\b",
    },
    {
        "key": "dual_alternator",
        "label": "High-output / dual alternator charging",
        "why": "Charges the house bank fast while you drive — no generator needed for normal use",
        "regex": r"dual alternator|2nd alternator|high.?output alternator",
    },
    {
        "key": "generator",
        "label": "Onboard generator",
        "why": "Backup power for hot days (roof A/C) and when solar can't keep up",
        "attr": "Generator Manufacturer",
        "regex": r"\bgenerator\b",
    },
    {
        "key": "fridge_12v",
        "label": "12V compressor fridge",
        "why": "No propane needed, runs off solar/battery, ice-cold reliably off-grid",
        "attr_true": "Refrigerator 12V",
        "regex": r"12.?v (compressor )?fridge|compressor fridge",
    },
    {
        "key": "hot_water_on_demand",
        "label": "Tankless / on-demand water heater",
        "why": "Endless hot water for two showers back-to-back — no waiting or running out",
        "attr": "Water Heater OnDemand",
        "regex": r"tankless|on.?demand water",
    },
    {
        "key": "four_season",
        "label": "4-season insulation / heated tanks",
        "why": "Livable in cold weather; protects plumbing from freezing",
        "attr": "Arctic/All Seasons",
        "regex": r"4.?season|four season|thinsulate|hydronic|heated tanks",
    },
    {
        "key": "shower",
        "label": "Real shower (wet bath in B, dry/split bath in C)",
        "why": "Two people living in it need an actual shower — wet bath = small but functional",
        "attr_contains": ("Bath", "shower"),
        "regex": r"\b(dry bath|wet bath|shower)\b",
    },
    {
        "key": "walk_around_bed",
        "label": "Walk-around queen bed",
        "why": "No crawling over each other nightly — the #1 couple complaint",
        "attr_ge": ("Queen Beds", 1),
        "regex": r"walk.?around (queen|king)",
    },
    {
        "key": "backup_camera",
        "label": "Backup / side-view cameras",
        "why": "A 24-30ft motorhome is impossible to park blind — cameras make it easy",
        "attr": "Backup Camera",
        "regex": r"backup cam|back.?up camera|side.?view cam",
    },
    {
        "key": "levelers",
        "label": "Automatic leveling jacks",
        "why": "Push a button and the rig is level — no boards and eyeballing",
        "attr": "Levelers",
        "regex": r"auto.?level|leveling jacks",
    },
    {
        "key": "auto_awning",
        "label": "Automatic patio awning",
        "why": "One-touch shade; the manual kind is a two-person chore",
        "attr": "Main Awning",
        "regex": r"auto awning|electric awning",
    },
    {
        "key": "swivel_seats",
        "label": "Swivel captain's chairs",
        "why": "Turns the cockpit into living room seating — huge in a small rig",
        "regex": r"swivel",
    },
    {
        "key": "low_miles",
        "label": "Low chassis miles (motorhome)",
        "why": "Engine/drivetrain wear drives the biggest repair bills on motorhomes",
        "low_miles": True,
    },
]
