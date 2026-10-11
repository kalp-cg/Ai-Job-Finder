"""
City company finder: OpenStreetMap + Wikidata + YC merged, filtered and cached; bulk watchlist adds.
All network calls are faked.

Run: python -m unittest discover -s tests
"""

from pathlib import Path
from unittest import mock

from src.opportunities import discover as dc
from tests.test_tracking import TempDataCase

PLACE = {"name": "Ahmedabad", "display_name": "Ahmedabad, Gujarat, India", "bbox": (22.9, 72.4, 23.1, 72.7),
         "center": (23.0, 72.55), "wikidata": "Q1070"}

OSM = {"elements": [
    {"tags": {"office": "it", "name": "TatvaSoft", "website": "tatvasoft.com"}},
    {"tags": {"office": "company", "name": "Hidden Brains Infotech"}},             # tech name, kept
    {"tags": {"office": "company", "name": "Sauber Environmental"}},               # not tech, dropped
    {"tags": {"office": "it", "name": "Airtel Store"}},                             # a shop, dropped
    {"tags": {"office": "it", "name": "Codekul Coaching Classes"}},                 # training, dropped
    {"tags": {"office": "it"}},                                                      # no name, dropped
    {"tags": {"office": "it", "name": "Colorado", "website": "https://embassyofficeparks.com/a"}},  # tech-park
    {"tags": {"office": "it", "name": "Congo", "website": "https://embassyofficeparks.com/b"}},     # buildings,
    {"tags": {"office": "it", "name": "Hudson", "website": "https://embassyofficeparks.com/c"}},    # all dropped
]}
WIKIDATA = {"results": {"bindings": [
    {"cLabel": {"value": "Hidden Brains Infotech Pvt. Ltd."}, "site": {"value": "https://www.hiddenbrains.com"}},
    {"cLabel": {"value": "Q123456"}},                                                # no English label, dropped
]}}
YC = [
    {"name": "Gujju AI", "website": "https://gujju.ai", "all_locations": "Ahmedabad, GJ, India", "isHiring": True, "status": "Active"},
    {"name": "Sleepy Co", "website": "https://sleepy.co", "all_locations": "Ahmedabad", "isHiring": False, "status": "Active"},
    {"name": "Bangalore Only", "website": "https://b.in", "all_locations": "Bengaluru", "isHiring": True, "status": "Active"},
]


def fake_get(url, params, accept="application/json"):
    resp = mock.Mock(status_code=200)
    resp.raise_for_status = mock.Mock()
    resp.json.return_value = OSM if "interpreter" in url else WIKIDATA
    return resp


class FinderTest(TempDataCase):
    def setUp(self):
        super().setUp()
        self.more = [mock.patch.object(dc, "CACHE_DIR", Path(self.tmp.name) / "cities"),
                     mock.patch.object(dc, "resolve_place", return_value=dict(PLACE)),
                     mock.patch.object(dc, "_get", side_effect=fake_get),
                     mock.patch.object(dc, "_yc_companies", return_value=YC)]
        for p in self.more:
            p.start()

    def tearDown(self):
        for p in self.more:
            p.stop()
        super().tearDown()

    def test_sources_are_merged_filtered_and_marked(self):
        from src.core.db import add_company
        add_company("TatvaSoft", website="https://tatvasoft.com", origin="seed")
        r = dc.find_companies("Ahmedabad")
        by_name = {c["name"]: c for c in r["companies"]}
        self.assertEqual(set(by_name), {"TatvaSoft", "Hidden Brains Infotech", "Gujju AI"})
        hb = by_name["Hidden Brains Infotech"]
        self.assertEqual(hb["sources"], ["OpenStreetMap", "Wikidata"])           # one company, two sources
        self.assertEqual(hb["website"], "https://www.hiddenbrains.com")           # filled in from Wikidata
        self.assertEqual(by_name["TatvaSoft"]["website"], "https://tatvasoft.com")  # scheme added
        self.assertTrue(by_name["TatvaSoft"]["watching"])
        self.assertFalse(by_name["Gujju AI"]["watching"])
        self.assertEqual(r["companies"][0]["name"], "Hidden Brains Infotech")     # most sources first
        self.assertEqual(r["errors"], {})

    def test_one_failing_source_is_reported_and_others_still_return(self):
        def broken(url, params, accept="application/json"):
            if "interpreter" in url:
                raise dc.requests.ConnectionError("Failed to resolve 'overpass-api.de'")
            return fake_get(url, params, accept)
        with mock.patch.object(dc, "_get", side_effect=broken), mock.patch.object(dc.time, "sleep"):
            r = dc.find_companies("Ahmedabad")
        self.assertIn("busy", r["errors"]["OpenStreetMap"])
        self.assertIn("Hidden Brains Infotech Pvt. Ltd.", [c["name"] for c in r["companies"]])
        self.assertFalse(any((Path(self.tmp.name) / "cities").glob("*.json")))  # half results aren't cached

    def test_results_are_cached_for_a_day(self):
        dc.find_companies("Ahmedabad")
        calls = dc._get.call_count
        dc.find_companies("Ahmedabad")
        self.assertEqual(dc._get.call_count, calls)
        dc.find_companies("Ahmedabad", refresh=True)
        self.assertGreater(dc._get.call_count, calls)

    def test_small_area_with_few_results_is_widened(self):
        empty = {"elements": []}
        calls = []

        def few(url, params, accept="application/json"):
            resp = fake_get(url, params, accept)
            if "interpreter" in url:
                calls.append(params["data"])
                resp.json.return_value = empty if len(calls) == 1 else OSM
            else:
                resp.json.return_value = {"results": {"bindings": []}}
            return resp
        with mock.patch.object(dc, "_get", side_effect=few), mock.patch.object(dc, "_yc_companies", return_value=[]):
            r = dc.find_companies("GIFT City")
        self.assertEqual(len(calls), 2)
        self.assertIn("15 km", r["note"])
        self.assertIn("TatvaSoft", [c["name"] for c in r["companies"]])

    def test_api_discover_and_bulk_watch(self):
        found = self.client.get("/api/discover", params={"city": "Ahmedabad"}).json()
        picks = [{"name": c["name"], "website": c["website"]} for c in found["companies"]]
        res = self.client.post("/api/companies/bulk", json={"city": "Ahmedabad", "companies": picks}).json()
        self.assertEqual(res["added"], 3)
        again = self.client.post("/api/companies/bulk", json={"city": "Ahmedabad", "companies": picks}).json()
        self.assertEqual(again["added"], 0)                                       # no duplicates
        watch = [c for c in self.client.get("/api/companies").json()["companies"] if c["origin"] == "city"]
        self.assertEqual({c["discovery_state"] for c in watch}, {"pending"})
        self.assertEqual({c["location_hint"] for c in watch}, {"Ahmedabad"})

    def test_unknown_place_is_a_clear_404(self):
        with mock.patch.object(dc, "resolve_place", side_effect=LookupError('couldn\'t find a place called "Xyzzy"')):
            res = self.client.get("/api/discover", params={"city": "Xyzzy"})
        self.assertEqual(res.status_code, 404)
        self.assertIn("Xyzzy", res.json()["detail"])

    def test_yc_aliases(self):
        bengaluru = dict(PLACE, name="Bengaluru")
        self.assertEqual([c["name"] for c in dc.from_yc(bengaluru, "Bangalore")], ["Bangalore Only"])
