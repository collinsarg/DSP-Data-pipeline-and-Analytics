IF COL_LENGTH('dsp.WST_UnplannedDelay','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.WST_UnplannedDelay
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        CONVERT(varchar(33), row_date, 126), '|',
        COALESCE(UPPER(LTRIM(RTRIM(station))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(unplanned_delay))),'#NULL#')
      )
    )
  ) PERSISTED;
END
GO

;WITH d AS (
  SELECT row_id,
         ROW_NUMBER() OVER (
           PARTITION BY CONVERT(varchar(33), row_date, 126),
                        UPPER(LTRIM(RTRIM(station))),
                        UPPER(LTRIM(RTRIM(unplanned_delay)))
           ORDER BY row_id DESC
         ) rn
  FROM dsp.WST_UnplannedDelay
)
DELETE FROM d WHERE rn > 1;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='UX_WST_Unplanned_RowSig' AND object_id=OBJECT_ID('dsp.WST_UnplannedDelay'))
  CREATE UNIQUE INDEX UX_WST_Unplanned_RowSig ON dsp.WST_UnplannedDelay(row_sig) WITH (IGNORE_DUP_KEY=ON);
GO
