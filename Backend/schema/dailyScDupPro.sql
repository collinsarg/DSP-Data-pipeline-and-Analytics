IF COL_LENGTH('dsp.DailyScorecard','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.DailyScorecard
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        COALESCE(UPPER(LTRIM(RTRIM(week_label))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(delivery_associate_id))),'#NULL#')
      )
    )
  ) PERSISTED;
END
GO

;WITH d AS (
  SELECT row_id,
         ROW_NUMBER() OVER (
           PARTITION BY UPPER(LTRIM(RTRIM(week_label))),
                        UPPER(LTRIM(RTRIM(delivery_associate_id)))
           ORDER BY row_id DESC
         ) rn
  FROM dsp.DailyScorecard
)
DELETE FROM d WHERE rn > 1;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='UX_DailyScorecard_RowSig' AND object_id=OBJECT_ID('dsp.DailyScorecard'))
  CREATE UNIQUE INDEX UX_DailyScorecard_RowSig ON dsp.DailyScorecard(row_sig) WITH (IGNORE_DUP_KEY=ON);
GO
