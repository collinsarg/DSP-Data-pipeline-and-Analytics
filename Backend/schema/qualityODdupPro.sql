IF COL_LENGTH('dsp.QualityOverviewDaily','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.QualityOverviewDaily
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        CONVERT(varchar(33), metric_date, 126), '|',
        COALESCE(UPPER(LTRIM(RTRIM(transporter_id))),'#NULL#')
      )
    )
  ) PERSISTED;
END
GO

;WITH d AS (
  SELECT row_id,
         ROW_NUMBER() OVER (
           PARTITION BY CONVERT(varchar(33), metric_date, 126),
                        UPPER(LTRIM(RTRIM(transporter_id)))
           ORDER BY row_id DESC
         ) rn
  FROM dsp.QualityOverviewDaily
)
DELETE FROM d WHERE rn > 1;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='UX_QOD_RowSig' AND object_id=OBJECT_ID('dsp.QualityOverviewDaily'))
  CREATE UNIQUE INDEX UX_QOD_RowSig ON dsp.QualityOverviewDaily(row_sig) WITH (IGNORE_DUP_KEY=ON);
GO
