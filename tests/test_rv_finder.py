"""Unit tests for the RV Finder service package.

These cover the pure logic the user relies on daily: junk-title rejection,
RV-type filtering (including motorhome aliases), the low-info deal-score cap,
life-ease feature detection, and MPG/livability readouts. No network / LLM.
"""
from __future__ import annotations

import pytest

from swarm_os.services.rv_finder.analysis import (
    _build_analysis,
    _is_motorhome_like,
    _title_motorhome,
)
from swarm_os.services.rv_finder.models import RVListing
from swarm_os.services.rv_finder.parsers import _is_junk_title
from swarm_os.services.rv_finder.service import _normalize_type_filter

MAKE_ATTRS = {"Brand": ["Winnebago"], "Model": ["Solis"]}

# The exact aggregator junk that previously surfaced as "best motorhome".
JUNK_TITLES = [
    "Used Class C RVs under $30,000 in Florida 12 24 48 96 Call Dealer Used 2016 Thor Motor $ 30000",
    "New & Used RVs for sale near me Call for price",
    "Motorhomes under $30k for sale",
    "Used Class A Motorhomes for sale",
    "RVs by Owner For Sale Search Results Page 3",
    "Used RVs near West Palm Beach, FL - RV Trader Port St. Lucie, FL La Mesa | RecVan - Port",
    "Used Motorhomes in Houston, TX - RV Trader",
    "New RVs near me By Owner Craigslist",
]

REAL_TITLES = [
    "2016 Winnebago View 24M Class C",
    "2018 Coachmen Leprechaun 260DS Class C",
    "2021 Forest River Rockwood 2906WS Fifth Wheel",
    "2022 Jayco Redhawk SE 29M Class C RV",
]


class TestJunkTitleFilter:
    @pytest.mark.parametrize("title", JUNK_TITLES)
    def test_rejects_aggregator_titles(self, title: str):
        assert _is_junk_title(title, model="thor" if "Thor" in title else "rockwood")

    @pytest.mark.parametrize("title", REAL_TITLES)
    def test_accepts_real_titles(self, title: str):
        assert not _is_junk_title(title, model="view" if "View" in title else "leprechaun")

    def test_rejects_junk_model_tokens(self):
        assert _is_junk_title("2016 Thor Motor Coach", model="Motor")
        assert _is_junk_title("Used RVs For Sale", model="Sale")

    def test_rejects_tabular_number_run(self):
        assert _is_junk_title("Some listing 12 24 48 96 2016 Thor")

    def test_accepts_trailer_with_slide_count(self):
        assert not _is_junk_title("2018 Coachmen Leprechaun 260DS 2 Slides Used", model="Leprechaun")


class TestTypeFilter:
    def test_motorhome_alias_matches_motorhomes(self):
        pred = _normalize_type_filter("motorhome")
        assert pred is not None
        assert pred(RVListing(title="A", rv_type="Class C Motorhome"))
        assert pred(RVListing(title="B", rv_type="Class B Motorhome"))
        assert not pred(RVListing(title="C", rv_type="Travel Trailer"))

    def test_camper_van_alias_matches_motorhomes(self):
        pred = _normalize_type_filter("camper van")
        assert pred(RVListing(title="D", rv_type="Class B Motorhome"))

    def test_class_c_matches_only_class_c(self):
        pred = _normalize_type_filter("class c")
        assert pred(RVListing(title="E", rv_type="Class C Motorhome"))
        assert not pred(RVListing(title="F", rv_type="Class B Motorhome"))
        assert not pred(RVListing(title="G", rv_type="Travel Trailer"))

    def test_fifth_wheel_matches_only_fifth_wheels(self):
        pred = _normalize_type_filter("fifth wheel")
        assert pred(RVListing(title="H", rv_type="Fifth Wheel"))
        assert not pred(RVListing(title="I", rv_type="Travel Trailer"))

    def test_garbage_term_yields_no_filter(self):
        assert _normalize_type_filter("xyzzy") is None


class TestMotorhomeDetection:
    def test_title_claims_motorhome(self):
        lst = RVListing(title="2016 Winnebago View 24M Class C", model="View")
        assert _title_motorhome(lst)
        assert _is_motorhome_like(lst)

    def test_title_claims_camper_van(self):
        lst = RVListing(title="2019 RAM Promaster Van Conversion", model="Promaster")
        assert _title_motorhome(lst)

    def test_trailer_with_class_b_only_in_boilerplate_is_not_title_motorhome(self):
        # Aggregator text said "Class B" but the listing's own title did not.
        # _title_motorhome is the guard that keeps such a trailer off the
        # "best motorhome" headline; _is_motorhome_like may still flag it for
        # per-listing analysis because the description is unambiguous.
        lst = RVListing(
            title="Fresno California Used 2019 Highland Ridge Mesa Ridge 2804RK",
            model="Fresno",
            rv_type="unknown",
            description="Browse Class B motorhomes and camper vans in Fresno, California...",
        )
        assert not _title_motorhome(lst)

    def test_classified_trailer_wins_over_model_keyword(self):
        # "Aurora" is a known-motorhome model token, but the source classified
        # this unit as a Fifth Wheel — that must take precedence.
        lst = RVListing(title="2024 Forest River Aurora 31KDS", model="Aurora",
                        rv_type="Fifth Wheel", make="Forest River")
        assert not _title_motorhome(lst)

    def test_trailer_brand_model_is_not_headline_motorhome(self):
        # Coachmen Freedom is a travel trailer; no headline motorhome claim.
        lst = RVListing(title="2023 Coachmen Freedom 29SE", model="Freedom 29SE",
                        rv_type="unknown", make="Coachmen")
        assert not _title_motorhome(lst)

    def test_motorhome_only_brand_with_year_is_motorhome(self):
        lst = RVListing(title="2015 Roadtrek 190 Popular", model="190 Popular",
                        rv_type="unknown", make="Roadtrek")
        assert _title_motorhome(lst)
        assert _is_motorhome_like(lst)

    def test_known_motorhome_model_without_type_keyword(self):
        lst = RVListing(title="2015 Roadtrek 190 Popular", model="190 Popular", rv_type="unknown")
        assert _is_motorhome_like(lst)

    def test_unknown_type_but_unambiguous_description(self):
        lst = RVListing(title="2016 Winnebago View", model="View", rv_type="unknown",
                        description="Class C motorhome, sleeps 4, Ford E-450")
        assert _is_motorhome_like(lst)


class TestAnalysisBasics:
    def test_low_info_snippet_never_scores_good_deal(self):
        lst = RVListing(
            title="2016 Thor Axis Class C",
            rv_type="Class C Motorhome",
            year=2016,
            make="Thor",
            model="Axis",
            price=30000,
            description="",
        )
        _build_analysis(lst, budget=30000)
        assert lst.analysis["score"]["score"] < 60.0
        assert lst.analysis["score"]["verdict"] not in ("Good Deal", "Excellent Deal")

    def test_structured_motorhome_gets_engine_and_mpg(self):
        lst = RVListing(
            title="2016 Winnebago View 24M Class C",
            rv_type="Class C Motorhome",
            year=2016,
            make="Winnebago",
            model="View",
            price=29000,
            attrs={
                "Engine Manufacturer": ["Ford"],
                "Engine Size": ["6.8L Triton"],
                "Transmission": ["Automatic"],
                "Chassis": ["Ford E-450"],
                "Mileage": ["42000"],
            },
        )
        _build_analysis(lst, budget=30000)
        assert _is_motorhome_like(lst)
        assert "Ford" in lst.analysis["engine"]["engine"]
        assert lst.analysis["mpg"]["mpg_estimate"] != "n/a"
        assert lst.analysis["life_ease"]["score"] >= 0

    def test_life_ease_detects_lithium_and_shower(self):
        lst = RVListing(
            title="2018 Roadtrek Zion Lithium",
            rv_type="Class B Motorhome",
            year=2018,
            make="Roadtrek",
            model="Zion",
            price=29999,
            description="Lithium battery bank, solar, shower",
            attrs={"Bath": ["Shower"], "Queen Beds": ["1"]},
        )
        _build_analysis(lst, budget=30000)
        le = lst.analysis["life_ease"]
        present = {c["key"] for c in le["checklist"] if c["present"]}
        assert "lithium" in present
        assert "solar" in present
        assert "shower" in present
        assert le["present_count"] >= 4

    def test_towable_mpg_is_guidance(self):
        lst = RVListing(
            title="2021 Forest River Rockwood 2906WS",
            rv_type="Travel Trailer",
            year=2021,
            make="Forest River",
            model="Rockwood",
            price=26000,
            sleeps=6,
            size_ft=33,
        )
        _build_analysis(lst, budget=30000)
        assert lst.analysis["mpg"]["mpg_estimate"] == "n/a"
        assert "tow" in lst.analysis["mpg"]["detail"].lower()

    def test_negotiation_tip_when_over_fair_value(self):
        lst = RVListing(
            title="2020 Grand Design Reflection 337RLS",
            rv_type="Fifth Wheel",
            year=2020,
            make="Grand Design",
            model="Reflection",
            price=90000,
        )
        _build_analysis(lst, budget=30000)
        tip = lst.analysis["negotiation_tip"] or ""
        assert "start negotiation" in tip
