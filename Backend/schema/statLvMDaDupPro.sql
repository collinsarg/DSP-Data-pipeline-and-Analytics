IF COL_LENGTH('dsp.StationLevelMetricsDaily','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.StationLevelMetricsDaily
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        CONVERT(varchar(33), metric_date, 126), '|',
        COALESCE(UPPER(LTRIM(RTRIM(dsp_code))),'#NULL#')
      )
    )
  ) PERSISTED;
END
GO

;WITH d AS (
  SELECT row_id,
         ROW_NUMBER() OVER (
           PARTITION BY CONVERT(varchar(33), metric_date, 126),
                        UPPER(LTRIM(RTRIM(dsp_code)))
           ORDER BY row_id DESC
         ) rn
  FROM dsp.StationLevelMetricsDaily
)
DELETE FROM d WHERE rn > 1;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='UX_SLM_Daily_RowSig' AND object_id=OBJECT_ID('dsp.StationLevelMetricsDaily'))
  CREATE UNIQUE INDEX UX_SLM_Daily_RowSig ON dsp.StationLevelMetricsDaily(row_sig) WITH (IGNORE_DUP_KEY=ON);
GO
