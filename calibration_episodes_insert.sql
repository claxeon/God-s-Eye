-- God's Eye — Historical Backfill SQL
-- Generated: 2026-06-09
-- Target table: calibration_episodes (schema_v2_state_vector.sql)

INSERT INTO calibration_episodes
  (episode_id, obs_date, episode_label,
   l1, l2, l3, l4, l5, l6, l7, l8, l9, l_cross,
   brent, vix, usd_jpy, us_10y_yield,
   scenario_realized, flash_crash_occurred, chokepoint_closure,
   days_to_resolution, notes)
VALUES
  ('2019_hormuz_tanker', '2019-07-19', '2019 Hormuz Tanker Seizures — IRGC Stena Impero', 0.5412, 0.4843, 0.3688, 0.28, 0.4317, 0.3, 0.35, 0.8176, 0.25, 0.4915, 65.1, 13.4, 107.3, 2.05, 'D', false, false, 72, 'IRGC seized UK-flagged tanker Stena Impero. US deployed carrier strike group. No Hormuz physical closure. Brent +$3-5. Resolved diplomatically. Base rate reference for IRGC mine/seizure behavior under pressure.'),
  ('2022_ukraine_lng', '2022-03-07', 'Ukraine War / European LNG Shock — Brent $130', 0.8523, 0.6912, 0.8397, 0.35, 0.8157, 0.55, 0.45, 0.5744, 0.28, 0.3656, 130.7, 36.5, 115.6, 1.83, 'C', false, false, 365, 'Brent crude hit $130/bbl. TTF natural gas spiked 10x. European LNG rush. Russian/Belarusian potash sanctioned → fertilizer crisis onset. No Hormuz component. Fed began hiking March 16. Leg 5 and Leg 2 analogue.'),
  ('2022_boj_ycc_stress', '2022-06-17', 'BOJ YCC Stress Onset — First Major Defense', 0.7267, 0.835, 0.8061, 0.38, 0.8002, 0.5, 0.48, 0.3775, 0.25, 0.6862, 113.7, 31.2, 134.5, 3.23, 'B', false, false, 180, 'BOJ began unlimited JGB purchase operations to defend YCC 0.25% cap. USD/JPY approached 135. Cross-cutting carry stress first emerged. No unwind — BOJ defended. Key contrast: Jun 2026 BOJ has NO reassurance option (already at 0.75%, YCC abandoned, markets pricing in >1.0%).'),
  ('2023_red_sea_onset', '2023-11-19', 'Houthi Red Sea Campaign Onset', 0.7096, 0.6699, 0.4058, 0.3, 0.7109, 0.52, 0.5, 0.8581, 0.35, 0.8211, 82.5, 14.3, 148.9, 4.44, 'C', false, false, 365, 'Houthis began attacking shipping after Oct 7 Hamas attack. Cape of Good Hope rerouting confirmed by Jan 2024. Lloyd''s war-risk surcharges activated. No physical closure. Leg 8 analogue for current state — difference: Jun 2026 Houthi declared blockade (Israeli ships), not just attacks of opportunity.'),
  ('2024_boj_flash_crash', '2024-08-05', 'Aug 2024 BOJ Flash Crash — Dress Rehearsal', 0.5979, 0.7061, 0.9809, 0.32, 0.6514, 0.48, 0.45, 0.4502, 0.38, 0.8851, 76.3, 65.0, 142.2, 3.79, 'B', true, false, 5, '15bp BOJ hike (Jul 31) → USD/JPY fell from 161 to 142. Nikkei –12%, VIX spike to 65, Bitcoin –20%. BOJ reassured market within 5 sessions. JPY carry unwind partial. CRITICAL DIFF from Jun 2026: 15bp vs 25bp; USD/JPY 161 vs 159; most importantly, BOJ has no reassurance option in Jun 2026 — YCC already abandoned, rates already at 0.75%, credibility committed.'),
  ('2026_current', '2026-06-08', 'Current State — God''s Eye Initialization (Jun 8, 2026)', 0.8353, 0.9487, 0.7595, 0.32, 0.8581, 0.45, 0.42, 0.9241, 0.38, 0.8927, NULL, 22.0, 159.0, 4.6, NULL, NULL, NULL, NULL, 'BOJ rate 0.75%, hike expected Jun 16-17. SPR 357.1 mmbbl (heel floor ~Jul 1). Houthi declared blockade Jun 8. TIC official T-bonds –$37.9B (Mar). Composite CB-D/CB-E threshold. Framework v4.2.')
ON CONFLICT (episode_id, obs_date) DO UPDATE SET
  l1 = EXCLUDED.l1, l2 = EXCLUDED.l2, l3 = EXCLUDED.l3,
  l4 = EXCLUDED.l4, l5 = EXCLUDED.l5, l6 = EXCLUDED.l6,
  l7 = EXCLUDED.l7, l8 = EXCLUDED.l8, l9 = EXCLUDED.l9,
  l_cross = EXCLUDED.l_cross,
  notes = EXCLUDED.notes;