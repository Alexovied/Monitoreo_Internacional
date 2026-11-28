from django.test import SimpleTestCase

from monitor import sightings

from .helpers import CNA, make_aircraft

OBSERVER = {"lat": CNA.lat, "lon": CNA.lon, "alt": 0.0}


def north_of_center(nm, **extra):
    """Aeronave al norte del centro de la zona, a ``nm`` millas."""
    return make_aircraft(lat=CNA.lat + nm / 60, lon=CNA.lon, dst=nm, **extra)


class GeometryTests(SimpleTestCase):
    def test_distance_and_bearing(self):
        dist, bearing = sightings.distance_bearing(CNA.lat, CNA.lon, CNA.lat + 1, CNA.lon)
        self.assertAlmostEqual(dist, 111_195, delta=50)  # un grado de latitud
        self.assertAlmostEqual(bearing, 0, places=3)
        _dist, bearing = sightings.distance_bearing(CNA.lat, CNA.lon, CNA.lat, CNA.lon + 1)
        self.assertAlmostEqual(bearing, 90, delta=0.5)

    def test_elevation_matches_the_sky_mode(self):
        # 10.000 m a 50 km: unos 11° (menos lo que baja por la curvatura).
        self.assertAlmostEqual(sightings.elevation_deg(50_000, 10_000), 11.15, delta=0.1)
        # Con el cielo levantado x4 se calcula como si estuviera a 12,5 km.
        self.assertAlmostEqual(sightings.elevation_deg(50_000, 10_000, lift=4), 38.3, delta=0.2)
        self.assertLess(sightings.elevation_deg(500_000, 10_000), 0)  # detrás del horizonte

    def test_separation(self):
        self.assertAlmostEqual(sightings.separation_deg((0, 0), (90, 0)), 90)
        self.assertAlmostEqual(sightings.separation_deg((10, 20), (10, 35)), 15)
        self.assertAlmostEqual(sightings.separation_deg((0, 89), (180, 89)), 2, places=3)  # cerca del cenit


class EvaluateTests(SimpleTestCase):
    def test_finds_the_aircraft_being_pointed_at(self):
        north = north_of_center(10, alt=10_000, flight="GLO1234")  # 18,5 km, ~9° de elevación
        other = make_aircraft("e48a1b", lat=CNA.lat, lon=CNA.lon + 0.3, flight="AZU4521")
        result = sightings.evaluate([other, north], OBSERVER, azimuth=2, elevation=10)
        self.assertEqual(result["match"]["label"], "GLO1234")
        self.assertLess(result["match"]["separation"], 3)
        self.assertEqual([c["label"] for c in result["candidates"]], ["GLO1234", "AZU4521"])

    def test_nothing_with_transponder_in_that_direction(self):
        north = north_of_center(10, alt=10_000)
        result = sightings.evaluate([north], OBSERVER, azimuth=180, elevation=30)
        self.assertIsNone(result["match"])
        self.assertEqual(len(result["candidates"]), 1)  # igual dice cuál es la más cercana
        self.assertGreater(result["candidates"][0]["separation"], 100)

    def test_empty_sky(self):
        self.assertEqual(sightings.evaluate([], OBSERVER, 0, 45),
                         {"match": None, "candidates": [], "threshold": sightings.MATCH_DEG})

    def test_lift_moves_the_aircraft_up_like_the_screen_does(self):
        north = north_of_center(27, alt=33_000)  # 50 km, 10.000 m: ~11° reales, ~38° con x4
        self.assertIsNone(sightings.evaluate([north], OBSERVER, 0, 38)["match"])
        self.assertIsNotNone(sightings.evaluate([north], OBSERVER, 0, 38, lift=4)["match"])

    def test_aircraft_without_altitude_are_skipped_and_ground_is_at_eye_level(self):
        unknown = north_of_center(5, alt=None)
        ground = make_aircraft("e48a1b", lat=CNA.lat + 0.02, lon=CNA.lon, alt="ground")
        result = sightings.evaluate([unknown, ground], OBSERVER, 0, 0)
        self.assertEqual([c["hex"] for c in result["candidates"]], ["e48a1b"])
        self.assertAlmostEqual(result["candidates"][0]["elevation"], 0, delta=0.1)
