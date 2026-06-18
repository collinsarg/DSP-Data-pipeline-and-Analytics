CREATE VIEW dsp.v_WeeklyScorecard_Aggregates
AS
WITH per_week AS (
    SELECT
        -- Parse year & week from label like '2025-W43'
        CAST(LEFT(ws.year_week_label, 4) AS int) AS year,
        CAST(RIGHT(ws.year_week_label, 2) AS int) AS week,

        COUNT(*) AS drivers_on_route,

        AVG(ws.overall_score)                   AS overall_score_avg,
        -- Safety metrics
        AVG(ws.speeding_event_rate)              AS speeding_event_rate_avg,
        AVG(ws.seatbelt_off_rate)          AS seatbelt_off_event_rate_avg,
        AVG(ws.sign_signal_viol_rate)       AS sign_signal_violation_rate_avg,
        AVG(ws.distractions_rate)           AS distraction_event_rate_avg,
        AVG(ws.following_dist_rate)    AS following_distance_event_rate_avg,

        -- Delivery quality / CDE metrics
        AVG(ws.dcr_metric)                          AS dcr_avg,        -- Delivery Completion Rate (%)
        AVG(ws.dsb_metric)                         AS dsb_dpmo_avg,       -- Delivery Success Behaviors DPMO
        AVG(ws.pod_metric)                 AS pod_avg,
        AVG(ws.cdf_dpmo)                         AS cdf_dpmo_avg,
        AVG(ws.ced_metric)                         AS ced_dpmo_avg,
        AVG(ws.psd_metric)                          AS psb_avg

        
    FROM dsp.WeeklyScorecard AS ws
    WHERE ws.overall_score IS NOT NULL   -- treat those as "on route"
    GROUP BY ws.year_week_label
)
SELECT *
FROM per_week;
GO
