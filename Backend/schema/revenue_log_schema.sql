/*****************************************************************************************
    Revenue Log Schema & Procedures (short station code; generic Package/Incentive)
    Safe to run multiple times. Creates objects if they do not exist or alters when needed.
******************************************************************************************/
SET NOCOUNT ON;

IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'dsp')
    EXEC('CREATE SCHEMA dsp AUTHORIZATION dbo');
GO

/*-------------------------
  1) RevenueRate (per week)
--------------------------*/
IF OBJECT_ID('dsp.RevenueRate','U') IS NULL
BEGIN
  CREATE TABLE dsp.RevenueRate(
    station_code    NVARCHAR(32)  NOT NULL,  -- e.g., DSW3
    service_type    NVARCHAR(200) NOT NULL,  -- e.g., '10Hr Rt', 'Package', 'Incentive', 'Amazon Cancellation', 'Training Classes'
    iso_year_week   NVARCHAR(16)  NOT NULL,  -- 'YYYY-W##'
    revenue_rate    DECIMAL(18,4) NOT NULL,
    CONSTRAINT PK_RevenueRate PRIMARY KEY (station_code, service_type, iso_year_week)
  );
END;
GO

/*----------------------------------
  2) RevenueLog (daily, atomic rows)
-----------------------------------*/
IF OBJECT_ID('dsp.RevenueLog','U') IS NULL
BEGIN
  CREATE TABLE dsp.RevenueLog(
    row_id                INT IDENTITY(1,1) PRIMARY KEY,
    iso_year_week         NVARCHAR(16)  NOT NULL,
    row_date              DATE          NOT NULL,
    station_code          NVARCHAR(32)  NULL,         -- short code like DSW3
    service_type          NVARCHAR(200) NULL,         -- generic names for Package/Incentive
    route_total           INT           NULL,         -- routes, cancels, or packages (count semantics)
    route_total_duration  INT           NULL,         -- hours for route-based rows (e.g., 10), else 0/NULL
    revenue_rate          DECIMAL(18,4) NULL,         -- snapshot of dsp.RevenueRate
    billable_hours        DECIMAL(18,4) NULL,         -- hours*routes for route-based; else 0
    revenue_total         DECIMAL(18,4) NULL
  );
  CREATE INDEX IX_RevenueLog_WeekSvc ON dsp.RevenueLog(iso_year_week, service_type);
  CREATE INDEX IX_RevenueLog_Date ON dsp.RevenueLog(row_date, station_code);
END;
GO

/*---------------------------------------------------
  3) Views (Daily/Weekly rollups using station_code)
----------------------------------------------------*/
CREATE OR ALTER VIEW dsp.vRevenueDaily AS
SELECT
  row_date,
  station_code,
  SUM(revenue_total)        AS revenue_total,
  SUM(billable_hours)       AS billable_hours,
  SUM(CASE WHEN service_type = N'Amazon Cancellation' THEN route_total ELSE 0 END) AS cancels,
  SUM(CASE WHEN service_type = N'Package'             THEN route_total ELSE 0 END) AS packages,
  SUM(CASE WHEN service_type = N'Incentive'           THEN route_total ELSE 0 END) AS incentives
FROM dsp.RevenueLog
GROUP BY row_date, station_code;
GO

CREATE OR ALTER VIEW dsp.vRevenueWeekly AS
SELECT
  iso_year_week,
  station_code,
  SUM(revenue_total)  AS revenue_total,
  SUM(billable_hours) AS billable_hours
FROM dsp.RevenueLog
GROUP BY iso_year_week, station_code;
GO

/*---------------------------------------------------
  4) Rebuild proc (delete window; Python repopulates)
----------------------------------------------------*/
CREATE OR ALTER PROCEDURE dsp.sp_RebuildRevenueLog
  @DateFrom DATE,
  @DateTo   DATE
AS
BEGIN
  SET NOCOUNT ON;
  DELETE RL
  FROM dsp.RevenueLog RL
  WHERE RL.row_date >= @DateFrom
    AND RL.row_date <= @DateTo;
END;
GO

/*---------------------------------------------------
  5) Optional helper: upsert rate row
----------------------------------------------------*/
CREATE OR ALTER PROCEDURE dsp.sp_RevenueRate_Upsert
  @station_code  NVARCHAR(32),
  @service_type  NVARCHAR(200),
  @iso_year_week NVARCHAR(16),
  @revenue_rate  DECIMAL(18,4)
AS
BEGIN
  SET NOCOUNT ON;
  MERGE dsp.RevenueRate AS tgt
  USING (SELECT @station_code AS station_code, @service_type AS service_type,
                @iso_year_week AS iso_year_week, @revenue_rate AS revenue_rate) AS src
  ON (tgt.station_code = src.station_code AND tgt.service_type = src.service_type AND tgt.iso_year_week = src.iso_year_week)
  WHEN MATCHED THEN UPDATE SET revenue_rate = src.revenue_rate
  WHEN NOT MATCHED THEN INSERT (station_code, service_type, iso_year_week, revenue_rate)
       VALUES (src.station_code, src.service_type, src.iso_year_week, src.revenue_rate);
END;
GO

/*---------------------------------------------------
  6) Seed examples (comment/uncomment to use)
----------------------------------------------------*/
/*
EXEC dsp.sp_RevenueRate_Upsert @station_code='DSW3', @service_type='10Hr Rt', @iso_year_week='2025-W36', @revenue_rate=36.25;
EXEC dsp.sp_RevenueRate_Upsert @station_code='DSW3', @service_type='Amazon Cancellation', @iso_year_week='2025-W36', @revenue_rate=200.00;
EXEC dsp.sp_RevenueRate_Upsert @station_code='DSW3', @service_type='Package', @iso_year_week='2025-W36', @revenue_rate=0.10;
EXEC dsp.sp_RevenueRate_Upsert @station_code='DSW3', @service_type='Incentive', @iso_year_week='2025-W36', @revenue_rate=0.15;
EXEC dsp.sp_RevenueRate_Upsert @station_code='DSW3', @service_type='Training Classes', @iso_year_week='2025-W36', @revenue_rate=29.50;
*/