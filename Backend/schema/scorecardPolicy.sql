CREATE TABLE dsp.ScorecardPolicy (
    metric_code            varchar(50)  NOT NULL PRIMARY KEY,    -- e.g. 'DCR', 'DSB_DPMO'
    metric_name            nvarchar(200) NOT NULL,
    category               varchar(50)  NOT NULL,                -- 'Safety', 'DeliveryQuality', 'TeamAndFleet', etc.

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

INSERT INTO dsp.ScorecardPolicy
(metric_code, metric_name, category, weight_overall_pct, direction,
 fantastic_threshold, great_threshold, fair_threshold,
 bias, scale_factor, is_active, is_coming_soon)
VALUES
-- Delivery Quality
('DCR',       'Delivery Completion Rate %',            'DeliveryQuality', 12.0, 'H',
  99.65,  99.00,  98.00,    0, 1, 1, 0),
('DSB_DPMO',  'Delivery Success Behaviors DPMO',       'DeliveryQuality', 12.0, 'L',
  50.0,   150.0,  400.0,    0, 1, 1, 0),
('POD',       'Photo-On-Delivery Acceptance %',        'DeliveryQuality',  3.0, 'H',
  98.0,   97.0,   95.0,     0, 1, 1, 0),

-- Customer Delivery Experience (18% total split into CDF / CED)
('CDF_DPMO',  'Customer Delivery Feedback DPMO',       'CustDeliveryExp',  6.0, 'L',
 1000.0,  3000.0,  8000.0,  0, 1, 1, 0),
('CED_DPMO',  'Customer Escalation Defect DPMO',       'CustDeliveryExp', 12.0, 'L',
   10.0,   40.0,   100.0,   0, 1, 1, 0),

-- Safety leaf metrics (weights are from the Appendix A list)
('SPD_RATE',  'Speeding Event Rate per 100 trips',     'Safety',          11.0, 'L',
   0.5,    1.5,   3.0,      0, 1, 1, 0),
('SEAT_RATE', 'Seatbelt-Off Rate per 100 trips',       'Safety',          11.0, 'L',
   0.5,    1.5,   3.0,      0, 1, 1, 0),
('SSV_RATE',  'Sign/Signal Violations per 100 trips',  'Safety',          11.0, 'L',
   0.5,    1.5,   3.0,      0, 1, 1, 0),
('DIST_RATE', 'Distractions Rate per 100 trips',       'Safety',           7.1, 'L',
   0.5,    1.5,   3.0,      0, 1, 1, 0),
('FD_RATE',   'Following Distance Rate per 100 trips', 'Safety',           4.7, 'L',
   0.5,    1.5,   3.0,      0, 1, 1, 0),

-- Team & Fleet
('TENURED',   'Tenured Workforce %',                   'TeamAndFleet',     5.0, 'H',
  90.0,   80.0,  70.0,      0, 1, 1, 0),
('FLEET',     'Fleet Execution Score (defect rate)',   'TeamAndFleet',     5.0, 'L',
   0.0,    3.0,  10.0,      0, 1, 1, 0),

-- Pickup Quality
('PSB',       'Pickup Success Behaviors defect rate',  'Pickup',           5.0, 'L',
   0.0,    3.0,  10.0,      0, 1, 1, 0);


CREATE FUNCTION dsp.fn_Score_HigherIsBetter (
    @value              decimal(18,4),
    @fantastic          decimal(18,4),
    @great              decimal(18,4),
    @fair               decimal(18,4)
)
RETURNS decimal(5,4)
AS
BEGIN
    DECLARE @score decimal(5,4);

    IF @value IS NULL
        SET @score = NULL;  -- or 0.0 if you prefer
    ELSE IF @value >= @fantastic
        SET @score = 1.00;
    ELSE IF @value >= @great
        SET @score = 0.80;
    ELSE IF @value >= @fair
        SET @score = 0.60;
    ELSE
        SET @score = 0.40;  -- "Poor"

    RETURN @score;
END;
GO

CREATE FUNCTION dsp.fn_Score_LowerIsBetter (
    @value              decimal(18,4),
    @fantastic          decimal(18,4),
    @great              decimal(18,4),
    @fair               decimal(18,4)
)
RETURNS decimal(5,4)
AS
BEGIN
    DECLARE @score decimal(5,4);

    IF @value IS NULL
        SET @score = NULL;
    ELSE IF @value <= @fantastic
        SET @score = 1.00;
    ELSE IF @value <= @great
        SET @score = 0.80;
    ELSE IF @value <= @fair
        SET @score = 0.60;
    ELSE
        SET @score = 0.40;

    RETURN @score;
END;
GO

