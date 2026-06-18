CREATE VIEW dsp.v_WeeklyProjectedScore
AS
SELECT
    w.dsp_short_code,
    w.station,
    w.week_index,             -- or week_start_date / iso_year + iso_week
    w.row_date_range,         -- whatever you use for the week label

    -- Core projected score 0–100
    CAST(
        CASE 
            -- If BOC or CAS non-compliant, you can force a floor (optional policy)
            WHEN w.breach_of_contract = 1 OR w.cas_compliant = 0 THEN 0.0
            ELSE
            (
                -- Weighted sum of metric_scores * metric_weight
                (
                    -- SAFETY
                    sc_spd.weight_overall_pct
                        * dsp.fn_Score_LowerIsBetter(
                              w.speeding_event_rate,
                              sc_spd.fantastic_threshold,
                              sc_spd.great_threshold,
                              sc_spd.fair_threshold
                          )
                  + sc_seat.weight_overall_pct
                        * dsp.fn_Score_LowerIsBetter(
                              w.seatbelt_off_rate,
                              sc_seat.fantastic_threshold,
                              sc_seat.great_threshold,
                              sc_seat.fair_threshold
                          )
                  + sc_ssv.weight_overall_pct
                        * dsp.fn_Score_LowerIsBetter(
                              w.sign_signal_violations_rate,
                              sc_ssv.fantastic_threshold,
                              sc_ssv.great_threshold,
                              sc_ssv.fair_threshold
                          )
                  + sc_dist.weight_overall_pct
                        * dsp.fn_Score_LowerIsBetter(
                              w.distractions_rate,
                              sc_dist.fantastic_threshold,
                              sc_dist.great_threshold,
                              sc_dist.fair_threshold
                          )
                  + sc_fd.weight_overall_pct
                        * dsp.fn_Score_LowerIsBetter(
                              w.following_distance_rate,
                              sc_fd.fantastic_threshold,
                              sc_fd.great_threshold,
                              sc_fd.fair_threshold
                          )

                    -- DELIVERY QUALITY
                  + sc_dcr.weight_overall_pct
                        * dsp.fn_Score_HigherIsBetter(
                              w.dcr_pct,
                              sc_dcr.fantastic_threshold,
                              sc_dcr.great_threshold,
                              sc_dcr.fair_threshold
                          )
                  + sc_dsb.weight_overall_pct
                        * dsp.fn_Score_LowerIsBetter(
                              w.dsb_dpmo,
                              sc_dsb.fantastic_threshold,
                              sc_dsb.great_threshold,
                              sc_dsb.fair_threshold
                          )
                  + sc_pod.weight_overall_pct
                        * dsp.fn_Score_HigherIsBetter(
                              w.pod_accept_pct,
                              sc_pod.fantastic_threshold,
                              sc_pod.great_threshold,
                              sc_pod.fair_threshold
                          )

                    -- CUSTOMER DELIVERY EXPERIENCE
                  + sc_cdf.weight_overall_pct
                        * dsp.fn_Score_LowerIsBetter(
                              w.cdf_dpmo,
                              sc_cdf.fantastic_threshold,
                              sc_cdf.great_threshold,
                              sc_cdf.fair_threshold
                          )
                  + sc_ced.weight_overall_pct
                        * dsp.fn_Score_LowerIsBetter(
                              w.ced_dpmo,
                              sc_ced.fantastic_threshold,
                              sc_ced.great_threshold,
                              sc_ced.fair_threshold
                          )

                    -- TEAM & FLEET
                  + sc_tenured.weight_overall_pct
                        * dsp.fn_Score_HigherIsBetter(
                              w.tenured_workforce_pct,
                              sc_tenured.fantastic_threshold,
                              sc_tenured.great_threshold,
                              sc_tenured.fair_threshold
                          )
                  + sc_fleet.weight_overall_pct
                        * dsp.fn_Score_LowerIsBetter(
                              w.fleet_exec_defect_rate,
                              sc_fleet.fantastic_threshold,
                              sc_fleet.great_threshold,
                              sc_fleet.fair_threshold
                          )

                    -- PICKUP QUALITY
                  + sc_psb.weight_overall_pct
                        * dsp.fn_Score_LowerIsBetter(
                              w.psb_defect_rate,
                              sc_psb.fantastic_threshold,
                              sc_psb.great_threshold,
                              sc_psb.fair_threshold
                          )
                )
                /
                NULLIF(
                    -- Sum of active metric weights (renormalize ≈ “Coming Soon” logic)
                    (CASE WHEN sc_spd.is_active = 1 THEN sc_spd.weight_overall_pct ELSE 0 END) +
                    (CASE WHEN sc_seat.is_active = 1 THEN sc_seat.weight_overall_pct ELSE 0 END) +
                    (CASE WHEN sc_ssv.is_active  = 1 THEN sc_ssv.weight_overall_pct  ELSE 0 END) +
                    (CASE WHEN sc_dist.is_active = 1 THEN sc_dist.weight_overall_pct ELSE 0 END) +
                    (CASE WHEN sc_fd.is_active   = 1 THEN sc_fd.weight_overall_pct   ELSE 0 END) +
                    (CASE WHEN sc_dcr.is_active  = 1 THEN sc_dcr.weight_overall_pct  ELSE 0 END) +
                    (CASE WHEN sc_dsb.is_active  = 1 THEN sc_dsb.weight_overall_pct  ELSE 0 END) +
                    (CASE WHEN sc_pod.is_active  = 1 THEN sc_pod.weight_overall_pct  ELSE 0 END) +
                    (CASE WHEN sc_cdf.is_active  = 1 THEN sc_cdf.weight_overall_pct  ELSE 0 END) +
                    (CASE WHEN sc_ced.is_active  = 1 THEN sc_ced.weight_overall_pct  ELSE 0 END) +
                    (CASE WHEN sc_tenured.is_active = 1 THEN sc_tenured.weight_overall_pct ELSE 0 END) +
                    (CASE WHEN sc_fleet.is_active   = 1 THEN sc_fleet.weight_overall_pct   ELSE 0 END) +
                    (CASE WHEN sc_psb.is_active     = 1 THEN sc_psb.weight_overall_pct     ELSE 0 END),
                    0
                )
            ) * 100.0
        END
        AS decimal(5,2)
    ) AS ProjectedOverallScorePct
FROM dsp.WeeklyOverview AS w

-- One row from policy table for each metric we care about
CROSS JOIN dsp.ScorecardPolicy AS sc_spd    -- SPEEDING_EVENT_RATE
CROSS JOIN dsp.ScorecardPolicy AS sc_seat   -- SEATBELT_OFF_RATE
CROSS JOIN dsp.ScorecardPolicy AS sc_ssv    -- SIGN/SIGNAL VIOLATIONS
CROSS JOIN dsp.ScorecardPolicy AS sc_dist   -- DISTRACTIONS
CROSS JOIN dsp.ScorecardPolicy AS sc_fd     -- FOLLOWING DISTANCE
CROSS JOIN dsp.ScorecardPolicy AS sc_dcr    -- DCR
CROSS JOIN dsp.ScorecardPolicy AS sc_dsb    -- DSB_DPMO
CROSS JOIN dsp.ScorecardPolicy AS sc_pod    -- POD
CROSS JOIN dsp.ScorecardPolicy AS sc_cdf    -- CDF_DPMO
CROSS JOIN dsp.ScorecardPolicy AS sc_ced    -- CED_DPMO
CROSS JOIN dsp.ScorecardPolicy AS sc_tenured -- TENURED
CROSS JOIN dsp.ScorecardPolicy AS sc_fleet   -- FLEET
CROSS JOIN dsp.ScorecardPolicy AS sc_psb     -- PSB
WHERE
    sc_spd.metric_code     = 'SPD_RATE'    AND
    sc_seat.metric_code    = 'SEAT_RATE'   AND
    sc_ssv.metric_code     = 'SSV_RATE'    AND
    sc_dist.metric_code    = 'DIST_RATE'   AND
    sc_fd.metric_code      = 'FD_RATE'     AND
    sc_dcr.metric_code     = 'DCR'         AND
    sc_dsb.metric_code     = 'DSB_DPMO'    AND
    sc_pod.metric_code     = 'POD'         AND
    sc_cdf.metric_code     = 'CDF_DPMO'    AND
    sc_ced.metric_code     = 'CED_DPMO'    AND
    sc_tenured.metric_code = 'TENURED'     AND
    sc_fleet.metric_code   = 'FLEET'       AND
    sc_psb.metric_code     = 'PSB';
GO
