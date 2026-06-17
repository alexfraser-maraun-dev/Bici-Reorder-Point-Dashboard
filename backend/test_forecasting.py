import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.forecasting import (
    seasonality_indices,
    seasonality_index_for_period,
    crostons_method,
    tsb_method,
    trend_coefficient,
    apply_forecast_enrichment,
    build_seasonal_profiles,
    resolve_category_profile,
    blend_seasonal_indices,
    seasonal_profile_for_item,
    build_seasonal_profile_response,
    lead_time_window_months,
    project_monthly_forecast,
    project_weeks_of_cover,
    _trend_factor,
)


class SeasonalityIndicesTest(unittest.TestCase):
    def test_flat_history_yields_all_ones(self):
        totals = {m: 100.0 for m in range(1, 13)}
        idx = seasonality_indices(totals)
        self.assertEqual(len(idx), 12)
        for v in idx.values():
            self.assertAlmostEqual(v, 1.0, places=9)

    def test_mean_index_normalizes_to_one(self):
        totals = {1: 50, 2: 50, 3: 50, 4: 50, 5: 50, 6: 50,
                  7: 200, 8: 200, 9: 150, 10: 100, 11: 80, 12: 70}
        idx = seasonality_indices(totals)
        mean = sum(idx.values()) / 12
        self.assertAlmostEqual(mean, 1.0, places=9)

    def test_peak_month_index_above_one(self):
        # Summer peak for bike retail.
        totals = {m: 100.0 for m in range(1, 13)}
        totals[7] = 300.0  # July triple
        idx = seasonality_indices(totals)
        self.assertGreater(idx[7], 1.0)
        self.assertLess(idx[1], 1.0)
        # Hand check: grand_total = 1100+200 = 1300; mean=1300/12=108.33
        # raw July = 300/108.33 = 2.7692; other 11 months raw = 100/108.33 = 0.9231
        # raw index mean = (2.7692 + 11*0.9231)/12 = 1.0769; July normalizes to
        # 2.7692 / 1.0769 = 2.5714, and July stays ~7x months 1-6 (300/100*0.857).
        self.assertAlmostEqual(idx[7], 2.5714285714, places=6)
        self.assertAlmostEqual(sum(idx.values()) / 12, 1.0, places=9)

    def test_empty_history_returns_neutral(self):
        idx = seasonality_indices({})
        self.assertEqual(idx, {m: 1.0 for m in range(1, 13)})

    def test_all_zero_history_returns_neutral(self):
        idx = seasonality_indices({m: 0 for m in range(1, 13)})
        self.assertEqual(idx, {m: 1.0 for m in range(1, 13)})

    def test_negative_totals_floored_to_zero(self):
        totals = {1: -50, 2: 100, 3: 100}
        idx = seasonality_indices(totals, num_periods=12)
        # Negative becomes 0 -> below-average month.
        self.assertLess(idx[1], 1.0)
        self.assertAlmostEqual(sum(idx.values()) / 12, 1.0, places=9)

    def test_sparse_history_missing_periods_treated_neutral(self):
        # Only two months have data.
        totals = {6: 100, 7: 100}
        idx = seasonality_indices(totals)
        # Missing months default to raw index 1.0 then renormalized.
        self.assertEqual(len(idx), 12)
        self.assertAlmostEqual(sum(idx.values()) / 12, 1.0, places=9)

    def test_weekly_cycle(self):
        totals = {w: 10.0 for w in range(1, 53)}
        idx = seasonality_indices(totals, num_periods=52)
        self.assertEqual(len(idx), 52)
        for v in idx.values():
            self.assertAlmostEqual(v, 1.0, places=9)

    def test_smoothing_pulls_toward_one(self):
        totals = {m: 100.0 for m in range(1, 13)}
        totals[7] = 400.0
        no_smooth = seasonality_indices(totals, smoothing=0.0)
        smoothed = seasonality_indices(totals, smoothing=2.0)
        # Smoothed July is closer to 1.0 than unsmoothed July.
        self.assertLess(smoothed[7], no_smooth[7])
        self.assertGreater(smoothed[7], 1.0)
        self.assertAlmostEqual(sum(smoothed.values()) / 12, 1.0, places=9)

    def test_min_total_gate_returns_neutral(self):
        totals = {1: 1, 2: 1}
        idx = seasonality_indices(totals, min_total=10)
        self.assertEqual(idx, {m: 1.0 for m in range(1, 13)})

    def test_negative_smoothing_raises(self):
        with self.assertRaises(ValueError):
            seasonality_indices({1: 5}, smoothing=-1)

    def test_index_lookup_helper(self):
        idx = {7: 1.5, 1: 0.5}
        self.assertEqual(seasonality_index_for_period(idx, 7), 1.5)
        self.assertEqual(seasonality_index_for_period(idx, 3), 1.0)  # missing -> default
        self.assertEqual(seasonality_index_for_period({}, 7), 1.0)   # empty -> default
        self.assertEqual(seasonality_index_for_period({7: 0}, 7), 1.0)  # zero -> default


class CrostonsMethodTest(unittest.TestCase):
    def test_empty_series(self):
        self.assertEqual(crostons_method([]), 0.0)

    def test_all_zeros(self):
        self.assertEqual(crostons_method([0, 0, 0, 0]), 0.0)

    def test_single_nonzero(self):
        # One unit at the first period -> interval 1 -> estimate == 5.
        self.assertAlmostEqual(crostons_method([5]), 5.0)

    def test_constant_demand_every_period(self):
        # Demand every period of size 4: interval == 1 -> estimate == 4.
        self.assertAlmostEqual(crostons_method([4, 4, 4, 4, 4]), 4.0, places=6)

    def test_intermittent_demand_hand_verified(self):
        # Demand of 2 every 4th period. z stays 2, x stays 4 -> 2/4 = 0.5.
        series = [0, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 2]
        result = crostons_method(series, alpha=0.1)
        self.assertAlmostEqual(result, 0.5, places=6)

    def test_lower_alpha_smooths(self):
        series = [0, 0, 5, 0, 0, 0, 0, 0, 1]
        low = crostons_method(series, alpha=0.05)
        high = crostons_method(series, alpha=0.5)
        # Both positive; exact values differ by smoothing.
        self.assertGreater(low, 0)
        self.assertGreater(high, 0)

    def test_invalid_alpha_raises(self):
        with self.assertRaises(ValueError):
            crostons_method([1, 0, 1], alpha=0)
        with self.assertRaises(ValueError):
            crostons_method([1, 0, 1], alpha=1.5)

    def test_none_values_treated_as_zero(self):
        # None periods count as zero-demand periods. First demand lands at
        # position 3 -> initial interval 3 -> estimate 3/3 = 1.0 (classic Croston
        # includes the leading gap in the first inter-demand interval).
        self.assertAlmostEqual(crostons_method([None, None, 3]), 1.0)
        # No leading gap: demand at position 1 -> interval 1 -> estimate 3.
        self.assertAlmostEqual(crostons_method([3, None, None]), 3.0)


class TSBMethodTest(unittest.TestCase):
    def test_empty_and_zeros(self):
        self.assertEqual(tsb_method([]), 0.0)
        self.assertEqual(tsb_method([0, 0, 0]), 0.0)

    def test_constant_demand(self):
        # Demand every period of 4: probability -> 1, size -> 4, estimate -> 4.
        result = tsb_method([4] * 30, alpha=0.3, beta=0.3)
        self.assertAlmostEqual(result, 4.0, places=2)

    def test_decays_when_demand_stops(self):
        # Demand early then long tail of zeros: TSB probability decays toward 0,
        # so the estimate should be small and below the early demand size.
        series = [3, 3, 3] + [0] * 30
        result = tsb_method(series, alpha=0.2, beta=0.2)
        self.assertGreater(result, 0.0)
        self.assertLess(result, 3.0)

    def test_intermittent_between_zero_and_size(self):
        series = [0, 0, 2, 0, 0, 2, 0, 0, 2, 0, 0, 2]
        result = tsb_method(series, alpha=0.1, beta=0.1)
        self.assertGreater(result, 0.0)
        self.assertLess(result, 2.0)

    def test_invalid_constants_raise(self):
        with self.assertRaises(ValueError):
            tsb_method([1, 0], alpha=0)
        with self.assertRaises(ValueError):
            tsb_method([1, 0], beta=2)


class TrendCoefficientTest(unittest.TestCase):
    def test_flat_is_neutral(self):
        self.assertAlmostEqual(trend_coefficient(1.0, 1.0, 1.0), 1.0, places=6)

    def test_rising_above_one(self):
        # recent double the baseline -> ratio 2.0.
        m = trend_coefficient(2.0, 1.0, 1.0)
        self.assertGreater(m, 1.0)
        self.assertLessEqual(m, 2.0)

    def test_cooling_below_one(self):
        m = trend_coefficient(0.5, 1.0, 1.0)
        self.assertLess(m, 1.0)
        self.assertGreaterEqual(m, 0.5)

    def test_upper_clamp(self):
        m = trend_coefficient(100.0, 1.0, 1.0)
        self.assertEqual(m, 2.0)

    def test_lower_clamp(self):
        m = trend_coefficient(0.001, 10.0, 10.0)
        self.assertEqual(m, 0.5)

    def test_accelerating_from_nothing_returns_max(self):
        self.assertEqual(trend_coefficient(5.0, 0.0, 0.0), 2.0)

    def test_cooling_to_nothing_returns_min(self):
        self.assertEqual(trend_coefficient(0.0, 3.0, 3.0), 0.5)

    def test_no_demand_is_neutral(self):
        self.assertEqual(trend_coefficient(0.0, 0.0, 0.0), 1.0)

    def test_sensitivity_zero_is_off(self):
        self.assertEqual(trend_coefficient(5.0, 1.0, 1.0, sensitivity=0.0), 1.0)

    def test_sensitivity_scales_movement(self):
        full = trend_coefficient(1.5, 1.0, 1.0, sensitivity=1.0)
        half = trend_coefficient(1.5, 1.0, 1.0, sensitivity=0.5)
        self.assertGreater(full, half)
        self.assertGreater(half, 1.0)

    def test_negative_inputs_floored(self):
        self.assertEqual(trend_coefficient(-5.0, -1.0, -1.0), 1.0)

    def test_custom_bounds(self):
        m = trend_coefficient(100.0, 1.0, 1.0, min_multiplier=0.8, max_multiplier=1.2)
        self.assertEqual(m, 1.2)

    def test_invalid_bounds_raise(self):
        with self.assertRaises(ValueError):
            trend_coefficient(1, 1, 1, min_multiplier=2.0, max_multiplier=1.0)
        with self.assertRaises(ValueError):
            trend_coefficient(1, 1, 1, min_multiplier=0)


class ApplyForecastEnrichmentTest(unittest.TestCase):
    def test_no_op_with_defaults(self):
        self.assertEqual(apply_forecast_enrichment(3.0), 3.0)

    def test_seasonality_only(self):
        self.assertAlmostEqual(apply_forecast_enrichment(2.0, seasonality_index=1.5), 3.0)

    def test_trend_only(self):
        self.assertAlmostEqual(apply_forecast_enrichment(2.0, trend_multiplier=0.5), 1.0)

    def test_combined(self):
        self.assertAlmostEqual(
            apply_forecast_enrichment(2.0, seasonality_index=1.5, trend_multiplier=2.0), 6.0
        )

    def test_croston_blend(self):
        # 50/50 blend of base 2.0 and croston 4.0 -> 3.0.
        self.assertAlmostEqual(
            apply_forecast_enrichment(2.0, croston_daily=4.0, croston_blend=0.5), 3.0
        )

    def test_croston_ignored_when_blend_zero(self):
        self.assertEqual(apply_forecast_enrichment(2.0, croston_daily=99.0, croston_blend=0.0), 2.0)

    def test_invalid_blend_raises(self):
        with self.assertRaises(ValueError):
            apply_forecast_enrichment(2.0, croston_blend=1.5)

    def test_negative_factors_floored(self):
        self.assertEqual(apply_forecast_enrichment(-5.0), 0.0)


class HierarchicalSeasonalityTest(unittest.TestCase):
    LEVELS = ("category_path", "category_top_level")
    PRIORITY = ("category_path", "category_top_level")

    def _records(self):
        # Two top-level categories with OPPOSITE peaks, with a small nonzero
        # baseline in every month (like real data: off-season is low, not absent):
        #   Trainers  -> winter peak (months 10-12 heavy)
        #   Nutrition -> summer peak (months 5-7 heavy)
        recs = []
        for mo in range(1, 13):
            trainers = 30.0 if mo in (10, 11, 12) else 2.0
            nutrition = 30.0 if mo in (5, 6, 7) else 2.0
            recs.append({"category_path": "Trainers/Smart", "category_top_level": "Trainers",
                         "month_of_year": mo, "total_units_sold": trainers})
            recs.append({"category_path": "Nutrition/Gels", "category_top_level": "Nutrition",
                         "month_of_year": mo, "total_units_sold": nutrition})
        return recs

    def test_build_profiles_opposite_peaks(self):
        profiles = build_seasonal_profiles(self._records(), self.LEVELS)
        trainers = profiles["category_top_level"]["Trainers"]
        nutrition = profiles["category_top_level"]["Nutrition"]
        # Trainers index high in Nov, low in June; Nutrition the reverse.
        self.assertGreater(trainers[11], 1.5)
        self.assertLess(trainers[6], 0.5)
        self.assertGreater(nutrition[6], 1.5)
        self.assertLess(nutrition[11], 0.5)
        # Both levels are present.
        self.assertIn("Trainers/Smart", profiles["category_path"])

    def test_min_group_total_filters_sparse_groups(self):
        recs = self._records()
        recs.append({"category_path": "Tiny/Thing", "category_top_level": "Tiny",
                     "month_of_year": 3, "total_units_sold": 1.0})
        profiles = build_seasonal_profiles(recs, self.LEVELS, min_group_total=5.0)
        self.assertNotIn("Tiny", profiles["category_top_level"])
        self.assertIn("Trainers", profiles["category_top_level"])

    def test_resolve_prefers_most_specific_then_falls_back(self):
        profiles = build_seasonal_profiles(self._records(), self.LEVELS)
        # Leaf path present -> resolves at category_path.
        lf, prof = resolve_category_profile(
            {"category_path": "Trainers/Smart", "category_top_level": "Trainers"},
            profiles, self.PRIORITY,
        )
        self.assertEqual(lf, "category_path")
        # Unknown leaf -> falls back to top level.
        lf2, prof2 = resolve_category_profile(
            {"category_path": "Trainers/Unknown", "category_top_level": "Trainers"},
            profiles, self.PRIORITY,
        )
        self.assertEqual(lf2, "category_top_level")
        # Nothing matches -> (None, None).
        lf3, prof3 = resolve_category_profile(
            {"category_path": "X", "category_top_level": "Y"}, profiles, self.PRIORITY,
        )
        self.assertIsNone(lf3)
        self.assertIsNone(prof3)

    def test_blend_weight_zero_returns_category(self):
        own = {m: 1.0 for m in range(1, 13)}
        own[1] = 5.0  # would-be own signal, but no history weight
        cat = {m: 1.0 for m in range(1, 13)}
        cat[6] = 3.0
        blended = blend_seasonal_indices(own, cat, own_history_periods=0, full_weight_periods=24)
        # w=0 -> category profile (renormalized).
        self.assertGreater(blended[6], blended[1])

    def test_blend_full_history_returns_own(self):
        own = {m: 1.0 for m in range(1, 13)}
        own[1] = 3.0
        cat = {m: 1.0 for m in range(1, 13)}
        cat[6] = 3.0
        blended = blend_seasonal_indices(own, cat, own_history_periods=24, full_weight_periods=24)
        self.assertGreater(blended[1], blended[6])

    def test_blend_renormalizes_to_mean_one(self):
        own = {m: 1.0 for m in range(1, 13)}
        own[1] = 4.0
        cat = {m: 1.0 for m in range(1, 13)}
        cat[6] = 4.0
        blended = blend_seasonal_indices(own, cat, own_history_periods=12, full_weight_periods=24)
        self.assertAlmostEqual(sum(blended.values()) / 12, 1.0, places=9)

    def test_blend_handles_missing_sources(self):
        cat = {m: 1.0 for m in range(1, 13)}
        cat[6] = 2.0
        self.assertEqual(blend_seasonal_indices({}, cat, 5), cat)
        self.assertEqual(blend_seasonal_indices(None, None, 5), {m: 1.0 for m in range(1, 13)})

    def test_seasonal_profile_for_item_end_to_end(self):
        profiles = build_seasonal_profiles(self._records(), self.LEVELS)
        # New SKU, no own history, in Trainers -> borrows Trainers winter shape.
        prof = seasonal_profile_for_item(
            own_period_totals=None,
            own_history_periods=0,
            category_values={"category_path": "Trainers/Unknown", "category_top_level": "Trainers"},
            profiles=profiles,
            level_priority=self.PRIORITY,
        )
        self.assertGreater(prof[11], prof[6])
        self.assertAlmostEqual(sum(prof.values()) / 12, 1.0, places=6)


class SeasonalProfileResponseTest(unittest.TestCase):
    """The /api/forecast/seasonal-profiles response builder."""

    LEVELS = ("category_top_level", "category_level_2")

    def _records(self):
        # Opposite peaks at top level, with a level_2 subcategory under each.
        recs = []
        for mo in range(1, 13):
            trainers = 30.0 if mo in (10, 11, 12) else 2.0
            nutrition = 30.0 if mo in (5, 6, 7) else 2.0
            recs.append({"category_top_level": "Trainers", "category_level_2": "Trainers/Smart",
                         "month_of_year": mo, "total_units_sold": trainers})
            recs.append({"category_top_level": "Nutrition", "category_level_2": "Nutrition/Gels",
                         "month_of_year": mo, "total_units_sold": nutrition})
        return recs

    def _by_label(self, response, label, level):
        for entry in response:
            if entry["category_label"] == label and entry["level"] == level:
                return entry
        self.fail(f"profile for {label!r} at {level!r} not found")

    def test_opposite_peaks_surface_in_response(self):
        resp = build_seasonal_profile_response(self._records(), level_fields=self.LEVELS)
        trainers = self._by_label(resp, "Trainers", "category_top_level")
        nutrition = self._by_label(resp, "Nutrition", "category_top_level")
        # Trainers peak in Nov (11), trough in June (6); Nutrition the reverse.
        self.assertGreater(trainers["indices"][11], 1.5)
        self.assertLess(trainers["indices"][6], 0.5)
        self.assertGreater(nutrition["indices"][6], 1.5)
        self.assertLess(nutrition["indices"][11], 0.5)

    def test_shape_and_fields(self):
        resp = build_seasonal_profile_response(self._records(), level_fields=self.LEVELS)
        # Two top-level + two level_2 categories = 4 entries.
        self.assertEqual(len(resp), 4)
        trainers = self._by_label(resp, "Trainers", "category_top_level")
        self.assertEqual(set(trainers["indices"].keys()), set(range(1, 13)))
        self.assertAlmostEqual(sum(trainers["indices"].values()) / 12, 1.0, places=3)
        # Trainers total = 3*30 + 9*2 = 108 units backing the profile.
        self.assertAlmostEqual(trainers["sample_units"], 108.0, places=2)

    def test_sorted_by_sample_units_desc(self):
        resp = build_seasonal_profile_response(self._records(), level_fields=self.LEVELS)
        units = [entry["sample_units"] for entry in resp]
        self.assertEqual(units, sorted(units, reverse=True))

    def test_empty_records_returns_empty(self):
        self.assertEqual(build_seasonal_profile_response([], level_fields=self.LEVELS), [])


class LeadTimeWindowTest(unittest.TestCase):
    def test_short_lead_time_lands_on_first_forecast_months(self):
        # Aug order (ref 8), ~14d lead -> floored to the first forecast month (Sep),
        # +30d coverage -> Sep..Oct. Never the reference month itself.
        w = lead_time_window_months(8, lead_time_days=14, coverage_days=30)
        self.assertEqual(w["start_month"], 9)
        self.assertEqual(w["end_month"], 10)

    def test_longer_lead_time_pushes_window_forward(self):
        # Aug (8), ~60d lead (2 months) + 30d coverage -> Oct..Nov (buy ahead of peak).
        w = lead_time_window_months(8, lead_time_days=60, coverage_days=30)
        self.assertEqual(w["start_month"], 10)
        self.assertEqual(w["end_month"], 11)

    def test_year_wraps_around(self):
        w = lead_time_window_months(12, lead_time_days=30, coverage_days=30)
        self.assertEqual(w["start_month"], 1)
        self.assertEqual(w["end_month"], 2)


class ProjectMonthlyForecastTest(unittest.TestCase):
    def test_flat_history_projects_flat_baseline(self):
        totals = {m: 120.0 for m in range(1, 13)}  # 1440 over 12 months -> 120/mo
        indices = {m: 1.0 for m in range(1, 13)}
        fc = project_monthly_forecast(totals, months_observed=12, indices=indices, reference_month=1)
        self.assertEqual(len(fc), 12)
        for point in fc:
            self.assertAlmostEqual(point["units"], 120.0, places=2)

    def test_seasonal_index_scales_the_month(self):
        totals = {m: 120.0 for m in range(1, 13)}
        indices = {m: 1.0 for m in range(1, 13)}
        indices[11] = 2.0  # November runs double
        fc = project_monthly_forecast(totals, months_observed=12, indices=indices, reference_month=1)
        nov = next(p for p in fc if p["month"] == 11)
        self.assertAlmostEqual(nov["units"], 240.0, places=2)
        self.assertAlmostEqual(nov["seasonal_index"], 2.0, places=4)

    def test_starts_month_after_reference(self):
        totals = {m: 12.0 for m in range(1, 13)}
        indices = {m: 1.0 for m in range(1, 13)}
        fc = project_monthly_forecast(totals, months_observed=12, indices=indices,
                                      reference_month=8, horizon_months=3)
        self.assertEqual([p["month"] for p in fc], [9, 10, 11])

    def test_no_history_yields_zero_baseline(self):
        fc = project_monthly_forecast({}, months_observed=0, indices={m: 1.0 for m in range(1, 13)},
                                      reference_month=1, horizon_months=2)
        self.assertTrue(all(p["units"] == 0.0 for p in fc))


class ForecastGrowthTrendTest(unittest.TestCase):
    FLAT_INDICES = {m: 1.0 for m in range(1, 13)}

    def _growing_series(self, monthly_growth=1.02, base=100.0, n=24):
        # Deseasonalizable monthly rows, oldest -> newest, growing each month.
        series = []
        for t in range(n):
            series.append({
                "year": 2024 + t // 12,
                "month": t % 12 + 1,
                "units": base * (monthly_growth ** t),
            })
        return series

    def _totals(self, series):
        totals = {}
        for row in series:
            totals[row["month"]] = totals.get(row["month"], 0.0) + row["units"]
        return totals

    def test_no_series_is_backward_compatible(self):
        # Without a history series the model is the original flat baseline.
        totals = {m: 120.0 for m in range(1, 13)}
        fc = project_monthly_forecast(totals, 12, self.FLAT_INDICES, reference_month=1)
        for point in fc:
            self.assertAlmostEqual(point["units"], 120.0, places=2)

    def test_growing_series_forecasts_above_flat_baseline(self):
        series = self._growing_series(monthly_growth=1.02, n=24)
        totals = self._totals(series)
        flat = project_monthly_forecast(totals, 24, self.FLAT_INDICES, reference_month=12)
        trended = project_monthly_forecast(
            totals, 24, self.FLAT_INDICES, reference_month=12, monthly_level_series=series
        )
        # Anchoring on the recent run-rate + upward growth -> above the flat average.
        self.assertGreater(trended[0]["units"], flat[0]["units"])
        # And it keeps climbing across the horizon (flat indices, so growth only).
        units = [p["units"] for p in trended]
        self.assertTrue(all(units[i] <= units[i + 1] for i in range(len(units) - 1)))

    def test_growth_is_damped(self):
        # Month-over-month growth factor shrinks over the horizon (phi < 1).
        series = self._growing_series(monthly_growth=1.03, n=24)
        totals = self._totals(series)
        fc = project_monthly_forecast(
            totals, 24, self.FLAT_INDICES, reference_month=12, monthly_level_series=series
        )
        early = fc[1]["units"] / fc[0]["units"]
        late = fc[11]["units"] / fc[10]["units"]
        self.assertGreater(early, late)
        self.assertGreater(early, 1.0)

    def test_growth_ratio_is_capped(self):
        # Absurd growth is clamped to the cap (default 1.30 annual).
        series = self._growing_series(monthly_growth=1.20, n=24)
        _, r = _trend_factor(series, self.FLAT_INDICES, months_observed=24)
        self.assertAlmostEqual(r, 1.30 ** (1.0 / 12.0), places=6)

    def test_shrinkage_weakens_growth_with_thin_history(self):
        series = self._growing_series(monthly_growth=1.02, n=24)
        _, r_full = _trend_factor(series, self.FLAT_INDICES, months_observed=24)
        _, r_thin = _trend_factor(series, self.FLAT_INDICES, months_observed=12)
        self.assertGreater(r_full, r_thin)
        self.assertGreater(r_thin, 1.0)


class ProjectWeeksOfCoverTest(unittest.TestCase):
    def test_no_velocity_is_always_healthy(self):
        cover = project_weeks_of_cover(on_hand=10, on_order=0, daily_velocity=0,
                                       indices=None, reference_month=1)
        self.assertEqual(len(cover), 12)
        self.assertTrue(all(c["stockout_risk"] == "healthy" for c in cover))

    def test_flat_demand_drains_inventory_over_time(self):
        # 100 units, 1/day flat -> ~14.3 wks at start, draining each month.
        cover = project_weeks_of_cover(on_hand=100, on_order=0, daily_velocity=1.0,
                                       indices={m: 1.0 for m in range(1, 13)}, reference_month=1)
        weeks = [c["weeks"] for c in cover]
        # Monotonically non-increasing as stock drains.
        self.assertTrue(all(weeks[i] >= weeks[i + 1] for i in range(len(weeks) - 1)))
        # Eventually hits a stockout (critical) within the year.
        self.assertEqual(cover[-1]["stockout_risk"], "critical")

    def test_on_order_adds_to_starting_inventory(self):
        low = project_weeks_of_cover(on_hand=10, on_order=0, daily_velocity=1.0,
                                     indices={m: 1.0 for m in range(1, 13)}, reference_month=1)
        high = project_weeks_of_cover(on_hand=10, on_order=90, daily_velocity=1.0,
                                      indices={m: 1.0 for m in range(1, 13)}, reference_month=1)
        self.assertGreater(high[0]["weeks"], low[0]["weeks"])

    def test_seasonal_peak_shortens_cover(self):
        # Same stock; a 3x peak month should show far fewer weeks of cover than flat.
        peak = {m: 1.0 for m in range(1, 13)}
        peak[2] = 3.0  # next month after Jan reference
        flat = project_weeks_of_cover(on_hand=40, on_order=0, daily_velocity=1.0,
                                      indices={m: 1.0 for m in range(1, 13)}, reference_month=1)
        ramp = project_weeks_of_cover(on_hand=40, on_order=0, daily_velocity=1.0,
                                      indices=peak, reference_month=1)
        # First forward month is February (index 2).
        self.assertLess(ramp[0]["weeks"], flat[0]["weeks"])


if __name__ == "__main__":
    unittest.main()
