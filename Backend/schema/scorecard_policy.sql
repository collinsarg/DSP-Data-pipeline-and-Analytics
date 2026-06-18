CREATE TABLE dsp.ScorecardPolicy (
    metric_code         varchar(50)    NOT NULL PRIMARY KEY,   -- internal code (e.g. 'DCR')
    weekly_metric_name  nvarchar(128)  NOT NULL,               -- must match WeeklyOverview.metric_name
    metric_name         nvarchar(200)  NOT NULL,               -- friendly label
    category            varchar(50)    NOT NULL,               -- 'Safety', 'DeliveryQuality', etc.

    -- Relative weight in the overall scorecard (from Appendix A)
    weight_overall_pct     decimal(5,2) NOT NULL,                -- e.g. 12.00 for DCR

    -- Direction: H = higher is better, L = lower is better
    direction              char(1)      NOT NULL
        CHECK (direction IN ('H','L')),

    -- Thresholds used to map raw metrics → [0, 1] score
    fantastic_threshold    decimal(18,4) NULL,
    great_threshold        decimal(18,4) NULL,
    fair_threshold         decimal(18,4) NULL,

    -- Optional extra tuning knobs if you later want linear scaling
    bias                   decimal(18,4) NULL DEFAULT 0,   -- intercept for that metric
    scale_factor           decimal(18,4) NULL DEFAULT 1,   -- multiply raw value before thresholding

    is_active              bit NOT NULL DEFAULT 1,         -- easy on/off switch per metric
    is_coming_soon         bit NOT NULL DEFAULT 0          -- mirrors Amazon “Coming Soon”
);