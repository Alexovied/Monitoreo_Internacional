from django.test import SimpleTestCase

from monitor import behavior
from monitor.behavior import Tracks

from .helpers import CNA, NOW, make_aircraft


def circling(steps, *, turn_per_step=30.0, gs=90.0, dst=8.0, start=NOW, step_s=10.0):
    """Posiciones de una aeronave que gira ``turn_per_step`` grados cada ``step_s``."""
    return [
        make_aircraft(dst=dst, gs=gs, track=(i * turn_per_step) % 360, now=start + i * step_s, alt=2500)
        for i in range(steps)
    ]


class TracksTests(SimpleTestCase):
    def test_keeps_only_new_positions_and_forgets_old_ones(self):
        tracks = Tracks(history_s=60)
        first = make_aircraft(now=NOW)
        self.assertTrue(tracks.record(first))
        self.assertFalse(tracks.record(first))  # misma posición: no se repite
        self.assertTrue(tracks.record(make_aircraft(now=NOW + 30)))
        self.assertEqual(len(tracks.get("e0b390")), 2)
        tracks.prune(NOW + 80)
        self.assertEqual(len(tracks.get("e0b390")), 1)
        tracks.prune(NOW + 500, keep={"e0b390"})
        self.assertIn("e0b390", tracks.points)  # con un incidente abierto no se olvida la aeronave
        tracks.prune(NOW + 500)
        self.assertNotIn("e0b390", tracks.points)

    def test_point_has_time_position_altitude_speed_and_heading(self):
        point = behavior.point_of(make_aircraft(now=NOW, seen_pos=0.5, track=None, true_heading=90.0))
        self.assertEqual(point, [NOW - 0.5, -35.0, -58.7, 3000, 142.0, 90.0])


class EmergencyTests(SimpleTestCase):
    def test_emergency_squawks_and_adsb_state(self):
        self.assertIn("7700", behavior.emergency_reason(make_aircraft(squawk="7700")))
        self.assertIn("secuestro", behavior.emergency_reason(make_aircraft(squawk="7500")))
        self.assertEqual(behavior.emergency_reason(make_aircraft(emergency="minfuel")), "combustible mínimo")
        self.assertIsNone(behavior.emergency_reason(make_aircraft(squawk="1200", emergency="none")))
        self.assertIsNone(behavior.emergency_reason(make_aircraft()))


class LoiterTests(SimpleTestCase):
    def points(self, aircraft):
        return [behavior.point_of(ac) for ac in aircraft]

    def test_two_orbits_add_up(self):
        flight = circling(25)  # 24 giros de 30° = 720°
        self.assertAlmostEqual(behavior.turned_degrees(self.points(flight), flight[-1].timestamp), 720)

    def test_counterclockwise_is_negative(self):
        flight = circling(13, turn_per_step=-30.0)
        self.assertAlmostEqual(behavior.turned_degrees(self.points(flight), flight[-1].timestamp), -360)

    def test_straight_flight_and_zigzag_do_not_count(self):
        straight = [make_aircraft(track=126.7, now=NOW + i * 10) for i in range(40)]
        self.assertEqual(behavior.turned_degrees(self.points(straight), straight[-1].timestamp), 0)
        zigzag = [make_aircraft(track=100 + (20 if i % 2 else -20), now=NOW + i * 10) for i in range(41)]
        self.assertLess(abs(behavior.turned_degrees(self.points(zigzag), zigzag[-1].timestamp)), 45)

    def test_slow_ground_and_gaps_are_ignored(self):
        slow = circling(25, gs=20.0)
        self.assertEqual(behavior.turned_degrees(self.points(slow), slow[-1].timestamp), 0)
        gaps = circling(25, step_s=120.0)  # un punto cada 2 minutos: no se sabe qué hizo en el medio
        self.assertEqual(behavior.turned_degrees(self.points(gaps), gaps[-1].timestamp), 0)

    def test_only_the_last_minutes_count(self):
        flight = circling(25)  # terminó de girar hace media hora
        self.assertEqual(behavior.turned_degrees(self.points(flight), flight[-1].timestamp + 1800), 0)

    def test_detect_flags_only_near_the_zone(self):
        near = circling(25, dst=8.0)
        found = behavior.detect(near[-1], self.points(near), [0, 0], CNA, near[-1].timestamp)
        self.assertEqual(found[behavior.LOITER]["turns"], 2.0)
        self.assertEqual(found[behavior.LOITER]["direction"], "horario")
        far = circling(25, dst=40.0)
        self.assertEqual(behavior.detect(far[-1], self.points(far), [0, 0], CNA, far[-1].timestamp), {})

    def test_watch_radius(self):
        self.assertAlmostEqual(behavior.watch_radius_nm(CNA), 16.2)  # 3 radios
        small = type(CNA)(name="x", lat=0, lon=0, radius_nm=1.0, floor_m=0, ceiling_m=1000)
        self.assertEqual(behavior.watch_radius_nm(small), 11.0)  # radio + 10 NM


class DescentAndLossTests(SimpleTestCase):
    def test_rapid_descent_needs_two_readings(self):
        self.assertTrue(behavior.is_rapid_descent([-4500, -5000]))
        self.assertFalse(behavior.is_rapid_descent([-1800, -5000]))
        self.assertFalse(behavior.is_rapid_descent([-5000]))
        self.assertFalse(behavior.is_rapid_descent([None, -5000]))

    def test_detect_reports_the_rate(self):
        ac = make_aircraft(dst=8.0, baro_rate=-5200.0)
        found = behavior.detect(ac, [behavior.point_of(ac)], [-4800.0, -5200.0], CNA, NOW)
        self.assertEqual(found, {behavior.RAPID_DESCENT: {"rate_fpm": -5200}})

    def test_emergency_is_reported_anywhere_in_coverage(self):
        ac = make_aircraft(dst=45.0, squawk="7600")
        found = behavior.detect(ac, [behavior.point_of(ac)], [None], CNA, NOW)
        self.assertEqual(list(found), [behavior.EMERGENCY])

    def test_lost_while_flying_ignores_landings(self):
        steady = [[NOW + i * 5, -35.0, -58.7, 3000, 140.0, 90.0] for i in range(8)]
        self.assertTrue(behavior.lost_while_flying(make_aircraft(alt=3000, gs=140.0), steady))
        self.assertTrue(behavior.lost_while_flying(make_aircraft(alt=3000, gs=140.0, baro_rate=-200.0), steady))
        self.assertFalse(behavior.lost_while_flying(make_aircraft(alt="ground", gs=140.0), steady))
        self.assertFalse(behavior.lost_while_flying(make_aircraft(alt=800, gs=60.0), steady))  # aterrizando: lento
        self.assertFalse(behavior.lost_while_flying(make_aircraft(alt=1500, gs=130.0, baro_rate=-700.0), steady))  # bajando

    def test_patchy_reception_is_not_a_lost_signal(self):
        flying = make_aircraft(alt=3000, gs=140.0)
        self.assertFalse(behavior.lost_while_flying(flying, []))
        self.assertFalse(behavior.lost_while_flying(flying, [[NOW + i * 5, 0, 0, 3000, 140.0, 90.0] for i in range(3)]))
        gaps = [[NOW + i * 50, 0, 0, 3000, 140.0, 90.0] for i in range(8)]  # una posición cada 50 s
        self.assertFalse(behavior.lost_while_flying(flying, gaps))

    def test_summaries_read_well(self):
        self.assertEqual(behavior.summary(behavior.ENTRY, None, "ZONA"), "Ingresó a ZONA")
        self.assertEqual(behavior.summary(behavior.LOITER, {"turns": 1.5, "direction": "horario"}, "ZONA"),
                         "Dio 1,5 vueltas (horario) cerca de ZONA")
        self.assertEqual(behavior.summary(behavior.RAPID_DESCENT, {"rate_fpm": -5200}, "ZONA"),
                         "Baja a 5.200 ft/min cerca de ZONA")
