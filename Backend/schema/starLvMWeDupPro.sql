IF COL_LENGTH('dsp.StationLevelMetricsWeekly','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.StationLevelMetricsWeekly
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        COALESCE(UPPER(LTRIM(RTRIM(iso_year_week))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(dsp_code))),'#NULL#')
      )
    )
  ) PERSISTED;
END
GO

;WITH d AS (
  SELECT row_id,
         ROW_NUMBER() OVER (
           PARTITION BY UPPER(LTRIM(RTRIM(iso_year_week))),
                        UPPER(LTRIM(RTRIM(dsp_code)))
           ORDER BY row_id DESC
         ) rn
  FROM dsp.StationLevelMetricsWeekly
)
DELETE FROM d WHERE rn > 1;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='UX_SLM_Weekly_RowSig' AND object_id=OBJECT_ID('dsp.StationLevelMetricsWeekly'))
  CREATE UNIQUE INDEX UX_SLM_Weekly_RowSig ON dsp.StationLevelMetricsWeekly(row_sig) WITH (IGNORE_DUP_KEY=ON);
GO
