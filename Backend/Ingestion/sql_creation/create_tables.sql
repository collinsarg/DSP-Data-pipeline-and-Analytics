-- =============================================================
Declare @DB_NAME NVARCHAR(128) = 'SNFL-Database';  -- change per deployment
Declare @SCHEMA_NAME NVARCHAR(128) = 'dsp';

-- Switch to target DB (assumes DB already exists)
USE [@DB_NAME];
GO

-- Ensure schema exists
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'@SCHEMA_NAME')
    EXEC('CREATE SCHEMA @SCHEMA_NAME;');
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'dsp')
    EXEC('CREATE SCHEMA dsp;');
GO

/* =======================
   Associate (people master) -- transporter_id is the PK
   ======================= */
IF OBJECT_ID('dsp.Associate', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.Associate(
    transporter_id      VARCHAR(16)  NOT NULL PRIMARY KEY, -- natural key
    full_name           NVARCHAR(200) NOT NULL,
    position_title      NVARCHAR(200) NULL,
    qualifications      NVARCHAR(400) NULL,
    id_expiration_date  DATE          NULL,
    personal_phone      NVARCHAR(32)  NULL,
    work_phone          NVARCHAR(32)  NULL,
    email               NVARCHAR(256) NULL,
    working_status      NVARCHAR(64)  NULL
  );
END;
GO

/* ===========
   Routes (snapshot)
   =========== */
IF OBJECT_ID('dsp.Routes', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.Routes(
    route_row_id        INT IDENTITY(1,1) PRIMARY KEY,
    snapshot_dt         DATETIME2(0)  NOT NULL DEFAULT (SYSUTCDATETIME()),
    route_code          NVARCHAR(32)  NOT NULL,
    dsp_name            NVARCHAR(200) NULL,
    transporter_id      VARCHAR(16)   NULL,
    driver_name         NVARCHAR(200) NULL,
    route_progress      NVARCHAR(64)  NULL,
    delivery_service_type NVARCHAR(200) NULL,
    route_duration_min  INT           NULL,
    all_stops           INT           NULL,
    stops_complete      INT           NULL,
    not_started_stops   INT           NULL
  );
  /* CREATE INDEX IX_Routes_routecode_time ON dsp.Routes(route_code, snapshot_dt);*/

  ALTER TABLE dsp.Routes  WITH NOCHECK
    ADD CONSTRAINT FK_Routes_Associate
    FOREIGN KEY (transporter_id) REFERENCES dsp.Associate(transporter_id);
END;
GO
-- Add row_sig if missing
IF COL_LENGTH('dsp.Routes','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.Routes
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        CONVERT(varchar(33), snapshot_dt, 126), '|',
        COALESCE(UPPER(LTRIM(RTRIM(route_code))),      '#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(transporter_id))),  '#NULL#')
      )
    )
  ) PERSISTED;
END
GO
-- Remove existing dupes BEFORE the unique index (safe to re-run)
IF NOT EXISTS (
  SELECT 1 FROM sys.indexes 
  WHERE name = N'UX_Routes_RowSig'
    AND object_id = OBJECT_ID(N'dsp.Routes')
)
BEGIN
  CREATE UNIQUE INDEX UX_Routes_RowSig
    ON dsp.Routes(row_sig)
    WITH (IGNORE_DUP_KEY = ON);
END
GO



/* =============
   Itineraries (snapshot)
   ============= */
-- 1) Create table if missing
IF OBJECT_ID(N'@SCHEMA_NAME.Itineraries', 'U') IS NULL
BEGIN
  CREATE TABLE "@SCHEMA_NAME".Itineraries
  (
    itin_row_id            INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Itin PRIMARY KEY,
    file_datetime          DATETIME2(0)      NOT NULL,        -- normalize to seconds
    transporter_id         NVARCHAR(64)      NOT NULL,
    driver_name            NVARCHAR(128)     NULL,
    dsp_name               NVARCHAR(128)     NULL,
    da_activity            NVARCHAR(64)      NULL,
    route_code             NVARCHAR(64)      NULL,
    progress_status        NVARCHAR(64)      NULL,
    projected_rts          INT               NULL,
    projected_ot_min       INT               NULL,
    delivery_service_type  NVARCHAR(200)     NULL,
    cortex_vin_number      NVARCHAR(64)      NULL,
    all_stops              INT               NULL,
    stops_complete         INT               NULL,
    not_started_stops      INT               NULL,
    total_packages         INT               NULL,
    cortex_avg_pace_sph    DECIMAL(9,2)      NULL,
    cortex_remaining_soc   DECIMAL(9,2)      NULL,
    app_sign_in_time       DATETIME2(0)      NULL,
    app_sign_out_time      DATETIME2(0)      NULL,
    cortex_last_stop_exec_time DATETIME2(0)  NULL,
    cortex_total_break_min INT               NULL,
    station_code           NVARCHAR(32)      NULL
  );
END
GO

-- 2) Backfill/upgrade columns if table already existed
IF COL_LENGTH('@SCHEMA_NAME'.Itineraries,station_code) IS NULL
  ALTER TABLE "@SCHEMA_NAME".Itineraries ADD station_code NVARCHAR(32) NULL;
GO

-- (Optional) align precision if you previously used DATETIME or millisecond precision
-- NOTE: do this only if safe for you.
-- ALTER TABLE $(SCHEMA_NAME).Itineraries ALTER COLUMN file_datetime DATETIME2(0) NOT NULL;
-- ALTER TABLE $(SCHEMA_NAME).Itineraries ALTER COLUMN app_sign_in_time DATETIME2(0) NULL;
-- ALTER TABLE $(SCHEMA_NAME).Itineraries ALTER COLUMN app_sign_out_time DATETIME2(0) NULL;
-- ALTER TABLE $(SCHEMA_NAME).Itineraries ALTER COLUMN cortex_last_stop_exec_time DATETIME2(0) NULL;
GO

-- 3) Add the persisted defensive hash (row_sig) if missing
IF COL_LENGTH('@SCHEMA_NAME'.Itineraries,'row_sig') IS NULL
BEGIN
  ALTER TABLE "@SCHEMA_NAME".Itineraries
  ADD row_sig AS CONVERT(VARBINARY(32),
        HASHBYTES('SHA2_256',
          CONCAT(
            CONVERT(varchar(33), file_datetime, 126), '|',
            COALESCE(UPPER(LTRIM(RTRIM(transporter_id))),'#NULL#'), '|',
            COALESCE(UPPER(LTRIM(RTRIM(route_code))),'#NULL#')
          )
        )
      ) PERSISTED;
END
GO

-- 4) (One-time) remove existing dupes BEFORE the unique index (safe to re-run)
;WITH d AS (
  SELECT itin_row_id,
         ROW_NUMBER() OVER (
           PARTITION BY 
             CONVERT(varchar(33), file_datetime, 126),
             UPPER(LTRIM(RTRIM(transporter_id))),
             UPPER(LTRIM(RTRIM(route_code)))
           ORDER BY itin_row_id DESC
         ) AS rn
  FROM "@SCHEMA_NAME".Itineraries
)
DELETE FROM d WHERE rn > 1;
GO

-- 5) Create unique index on the hash (idempotent)
IF NOT EXISTS (
  SELECT 1 FROM sys.indexes 
  WHERE name = N'UX_Itin_RowSig' 
    AND object_id = OBJECT_ID(N'$(SCHEMA_NAME).Itineraries')
)
BEGIN
  CREATE UNIQUE INDEX UX_Itin_RowSig
    ON "@SCHEMA_NAME".Itineraries(row_sig)
    WITH (IGNORE_DUP_KEY = ON);
END
GO

-- 6) (Optional) Data quality guardrails
IF NOT EXISTS (
  SELECT 1 FROM sys.check_constraints 
  WHERE name = N'CK_Itin_Transporter_NotBlank'
    AND parent_object_id = OBJECT_ID(N'$(SCHEMA_NAME).Itineraries')
)
BEGIN
  ALTER TABLE "@SCHEMA_NAME".Itineraries
    ADD CONSTRAINT CK_Itin_Transporter_NotBlank
    CHECK (NULLIF(LTRIM(RTRIM(transporter_id)),'') IS NOT NULL);
END
GO


/* ===========
   Netradyne events
   =========== */
IF OBJECT_ID('dsp.NetradyneEvents', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.NetradyneEvents(
    event_row_id        INT IDENTITY(1,1) PRIMARY KEY,
    event_date          DATE          NOT NULL,
    delivery_associate  NVARCHAR(200) NULL,
    transporter_id      VARCHAR(16)   NULL,
    event_id            BIGINT        NULL,
    event_datetime      DATETIME2(0)  NULL,
    vin                 NVARCHAR(32)  NULL,
    oss_impact          NVARCHAR(8)   NULL,
    metric_type         NVARCHAR(100) NULL,
    metric_subtype      NVARCHAR(100) NULL,
    source              NVARCHAR(64)  NULL,
    video_link          NVARCHAR(512) NULL,
    review_details      NVARCHAR(1000) NULL
  );
 
  ALTER TABLE dsp.NetradyneEvents  WITH NOCHECK
    ADD CONSTRAINT FK_Netradyne_Associate
    FOREIGN KEY (transporter_id) REFERENCES dsp.Associate(transporter_id);
END;
GO

/* ============
   Fleet vehicles
   ============ */
IF OBJECT_ID('dsp.FleetVehicles', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.FleetVehicles(
    vehicle_id          INT IDENTITY(1,1) PRIMARY KEY,
    vin                 NVARCHAR(32)  NOT NULL,
    service_type        NVARCHAR(200) NULL,
    vehicle_name        NVARCHAR(200) NULL,
    license_plate       NVARCHAR(64)  NULL,
    make                NVARCHAR(64)  NULL,
    model               NVARCHAR(128) NULL,
    sub_model           NVARCHAR(200) NULL,
    vehicle_status      NVARCHAR(64)  NULL,
    status_priority     INT           NULL,
    status_reason_code  NVARCHAR(64)  NULL,
    status_reason_msg   NVARCHAR(256) NULL,
    operational_status  NVARCHAR(64)  NULL,
    status_search_value NVARCHAR(64)  NULL,
    subcontractor_name  NVARCHAR(200) NULL,
    vehicle_provider    NVARCHAR(128) NULL,
    vehicle_reg_type    NVARCHAR(64)  NULL,
    vehicle_year        INT           NULL,
    vehicle_type        NVARCHAR(128) NULL,
    ownership_type      NVARCHAR(64)  NULL,
    ownership_start     DATE          NULL,
    ownership_end       DATE          NULL,
    pm_stats            NVARCHAR(256) NULL,
    registration_expiry DATE          NULL,
    registered_state    NVARCHAR(64)  NULL,
    service_tier        NVARCHAR(128) NULL,
    station_code        NVARCHAR(32)  NULL,
    payload             NVARCHAR(64)  NULL,
    cubic_capacity      NVARCHAR(64)  NULL,
    CONSTRAINT UQ_Fleet_VIN UNIQUE (vin)
  );
END;
GO

/* =================
   Daily Overview (metric_name, metric_date)
   ================= */
IF OBJECT_ID('dsp.DailyOverview', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.DailyOverview(
    metric_row_id   INT IDENTITY(1,1) PRIMARY KEY,
    metric_date     DATE          NOT NULL,
    metric_name     NVARCHAR(128) NOT NULL,
    metric_value    DECIMAL(18,4) NULL,
    source_note     NVARCHAR(64)  NULL
  );
END;
GO

/* =================
   Quality Overview (Daily per DA)
   ================= */
IF OBJECT_ID('dsp.QualityOverviewDaily', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.QualityOverviewDaily(
    row_id            INT IDENTITY(1,1) PRIMARY KEY,
    metric_date       DATE          NOT NULL,
    delivery_associate NVARCHAR(200) NULL,
    transporter_id    VARCHAR(16)   NULL,
    packages_delivered INT          NULL,
    routes_completed  INT           NULL,
    dcr_percent       DECIMAL(6,2)  NULL,
    pod_percent       DECIMAL(6,2)  NULL,
    dsb_count         INT           NULL
  );

  ALTER TABLE dsp.QualityOverviewDaily  WITH NOCHECK
    ADD CONSTRAINT FK_QualDaily_Associate
    FOREIGN KEY (transporter_id) REFERENCES dsp.Associate(transporter_id);
END;
GO

/* =================
   Daily Scorecard (per DA, daily dump)
   (No transporter_id column in source; leaving as-is.)
   ================= */
IF OBJECT_ID('dsp.DailyScorecard', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.DailyScorecard(
    row_id                  INT IDENTITY(1,1) PRIMARY KEY,
    week_label              NVARCHAR(32)  NULL,
    delivery_associate_name NVARCHAR(200) NULL,
    delivery_associate_id   NVARCHAR(64)  NULL,
    delivered_packages      INT           NULL,
    packages_dnr            INT           NULL,
    dsb_count               INT           NULL,
    dsb_dpmo                INT           NULL,
    dispatched_packages     INT           NULL,
    packages_rts            INT           NULL,
    packages_rts_percent    DECIMAL(9,4)  NULL,
    rts_dpmo                INT           NULL
  );
END;
GO

/* =========================
   Station Level Metrics Daily
   ========================= */
IF OBJECT_ID('dsp.StationLevelMetricsDaily', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.StationLevelMetricsDaily(
    row_id           INT IDENTITY(1,1) PRIMARY KEY,
    metric_date      DATE          NOT NULL,
    dsp_code         NVARCHAR(32)  NULL,
    dispatched_pkg   INT           NULL,
    delivered_pkg    INT           NULL,
    dnr              INT           NULL,
    dnr_dpmo         INT           NULL,
    rts              INT           NULL,
    rts_percent      DECIMAL(6,3)  NULL,
    rts_dpmo         INT           NULL
  );
END;
GO

/* =========================
   Station Level Metrics Weekly
   ========================= */
IF OBJECT_ID('dsp.StationLevelMetricsWeekly', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.StationLevelMetricsWeekly(
    row_id           INT IDENTITY(1,1) PRIMARY KEY,
    iso_year_week    NVARCHAR(16)  NOT NULL,
    dsp_code         NVARCHAR(32)  NULL,
    dispatched_pkg   INT           NULL,
    delivered_pkg    INT           NULL,
    dnr              INT           NULL,
    dnr_dpmo         INT           NULL,
    rts              INT           NULL,
    rts_percent      DECIMAL(6,3)  NULL,
    rts_dpmo         INT           NULL
  );
END;
GO

/* =================
   WST - Delivered Packages
   ================= */
IF OBJECT_ID('dsp.WST_DeliveredPackages', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.WST_DeliveredPackages(
    row_id          INT IDENTITY(1,1) PRIMARY KEY,
    metric_date     DATE          NOT NULL,
    station         NVARCHAR(128) NULL,
    dsp_short_code  NVARCHAR(32)  NULL,
    package_count   INT           NULL,
    package_details NVARCHAR(128) NULL,
    package_type    NVARCHAR(128) NULL
  );
END;
GO

/* =================
   WST - Service Details
   ================= */
IF OBJECT_ID('dsp.WST_ServiceDetails', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.WST_ServiceDetails(
    row_id                  INT IDENTITY(1,1) PRIMARY KEY,
    row_date                DATE          NULL,
    station                 NVARCHAR(128) NULL,
    dsp_short_code          NVARCHAR(32)  NULL,
    delivery_associate      NVARCHAR(200) NULL,
    route_code              NVARCHAR(32)  NULL,
    service_type            NVARCHAR(200) NULL,
    planned_duration_label  NVARCHAR(32)  NULL,
    login_ts                DATETIME2(3)  NULL,
    logout_ts               DATETIME2(3)  NULL,
    total_distance_planned  INT           NULL,
    total_distance_allow    INT           NULL,
    distance_unit           NVARCHAR(32)  NULL,
    shipments_delivered     INT           NULL,
    shipments_returned      INT           NULL,
    pickup_packages         INT           NULL,
    excluded_flag           NVARCHAR(8)   NULL
  );
END;
GO

/* =================
   WST - Unplanned Delay Weekly Report
   ================= */
IF OBJECT_ID('dsp.WST_UnplannedDelay', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.WST_UnplannedDelay(
    row_id          INT IDENTITY(1,1) PRIMARY KEY,
    row_date        DATE          NULL,
    station         NVARCHAR(128) NULL,
    dsp_short_code  NVARCHAR(32)  NULL,
    unplanned_delay NVARCHAR(256) NULL,
    total_delay_min INT           NULL,
    impacted_routes INT           NULL,
    notes           NVARCHAR(1000) NULL
  );
END;
GO

/* =================
   WST - Weekly Report (routes & distance summary)
   ================= */
IF OBJECT_ID('dsp.WST_WeeklyReport', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.WST_WeeklyReport(
    row_id                 INT IDENTITY(1,1) PRIMARY KEY,
    row_date               DATE          NOT NULL,
    station                NVARCHAR(128) NULL,
    dsp_short_code         NVARCHAR(32)  NULL,
    service_type           NVARCHAR(200) NULL,
    planned_duration_label NVARCHAR(32)  NULL,
    total_distance_planned INT           NULL,
    total_distance_allow   INT           NULL,
    planned_distance_unit  NVARCHAR(32)  NULL,
    amzl_late_cancel       NVARCHAR(32)  NULL,
    dsp_late_cancel        NVARCHAR(32)  NULL,
    quick_coverage         NVARCHAR(32)  NULL,
    accepted               NVARCHAR(32)  NULL,
    completed_routes       INT           NULL
  );
END;
GO

/* =================
   Weekly Scorecard (per DA)
   ================= */
IF OBJECT_ID('dsp.WeeklyScorecard', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.WeeklyScorecard (
      row_id                      INT IDENTITY(1,1) PRIMARY KEY,
      year_week_label             NVARCHAR(16)  NULL,
      delivery_associate_name     NVARCHAR(200) NULL,
      transporter_id              VARCHAR(32)   NULL,

      overall_standing            NVARCHAR(50)  NULL,
      overall_score               DECIMAL(6,2)  NULL,

      fico_metric                 NVARCHAR(64)  NULL,
      fico_tier                   NVARCHAR(50)  NULL,
      fico_score                  DECIMAL(6,2)  NULL,

      speeding_event_rate         DECIMAL(9,4)  NULL,
      speeding_event_rate_tier    NVARCHAR(50)  NULL,
      speeding_event_rate_score   DECIMAL(6,2)  NULL,

      seatbelt_off_rate           DECIMAL(9,4)  NULL,
      seatbelt_off_rate_tier      NVARCHAR(50)  NULL,
      seatbelt_off_rate_score     DECIMAL(6,2)  NULL,

      distractions_rate           DECIMAL(9,4)  NULL,
      distractions_rate_tier      NVARCHAR(50)  NULL,
      distractions_rate_score     DECIMAL(6,2)  NULL,

      sign_signal_viol_rate       DECIMAL(9,4)  NULL,
      sign_signal_viol_rate_tier  NVARCHAR(50)  NULL,
      sign_signal_viol_rate_score DECIMAL(6,2)  NULL,

      following_dist_rate         DECIMAL(9,4)  NULL,
      following_dist_rate_tier    NVARCHAR(50)  NULL,
      following_dist_rate_score   DECIMAL(6,2)  NULL,

      cdf_dpmo                    DECIMAL(9,2)  NULL,
      cdf_dpmo_tier               NVARCHAR(50)  NULL,
      cdf_dpmo_score              DECIMAL(6,2)  NULL,

      ced_metric                  DECIMAL(9,2)  NULL,
      ced_tier                    NVARCHAR(50)  NULL,
      ced_score                   DECIMAL(6,2)  NULL,

      dcr_metric                  DECIMAL(9,2)  NULL,
      dcr_tier                    NVARCHAR(50)  NULL,
      dcr_score                   DECIMAL(6,2)  NULL,

      dsb_metric                  DECIMAL(9,2)  NULL,
      dsb_dpmo_tier               NVARCHAR(50)  NULL,
      dsb_dpmo_score              DECIMAL(6,2)  NULL,

      pod_metric                  DECIMAL(9,2)  NULL,
      pod_tier                    NVARCHAR(50)  NULL,
      pod_score                   DECIMAL(6,2)  NULL,

      psb_metric                  DECIMAL(9,2)  NULL,
      psb_tier                    NVARCHAR(50)  NULL,
      psb_score                   DECIMAL(6,2)  NULL,

      packages_delivered          INT           NULL
  );

END;
GO

/* =================
   Weekly Overview (metric_name, iso_year_week)
   ================= */
IF OBJECT_ID('dsp.WeeklyOverview', 'U') IS NULL
BEGIN
  CREATE TABLE dsp.WeeklyOverview(
    metric_row_id   INT IDENTITY(1,1) PRIMARY KEY,
    iso_year_week   NVARCHAR(16)  NOT NULL,
    metric_name     NVARCHAR(128) NOT NULL,
    metric_value    DECIMAL(18,4) NULL,
    source_note     NVARCHAR(64)  NULL
  );
END;
GO

