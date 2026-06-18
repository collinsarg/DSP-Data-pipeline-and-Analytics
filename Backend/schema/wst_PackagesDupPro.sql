IF COL_LENGTH('dsp.WST_DeliveredPackages','row_sig') IS NULL
BEGIN
  ALTER TABLE dsp.WST_DeliveredPackages
  ADD row_sig AS CONVERT(VARBINARY(32),
    HASHBYTES('SHA2_256',
      CONCAT(
        CONVERT(varchar(33), metric_date, 126), '|',
        COALESCE(UPPER(LTRIM(RTRIM(station))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(package_type))),'#NULL#'), '|',
        COALESCE(UPPER(LTRIM(RTRIM(package_details))),'#NULL#')
      )
    )
  ) PERSISTED;
END
GO

;WITH d AS (
  SELECT row_id,
         ROW_NUMBER() OVER (
           PARTITION BY CONVERT(varchar(33), metric_date, 126),
                        UPPER(LTRIM(RTRIM(station))),
                        UPPER(LTRIM(RTRIM(package_type))),
                        UPPER(LTRIM(RTRIM(package_details)))
           ORDER BY row_id DESC
         ) rn
  FROM dsp.WST_DeliveredPackages
)
DELETE FROM d WHERE rn > 1;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='UX_WST_Delivered_RowSig' AND object_id=OBJECT_ID('dsp.WST_DeliveredPackages'))
  CREATE UNIQUE INDEX UX_WST_Delivered_RowSig ON dsp.WST_DeliveredPackages(row_sig) WITH (IGNORE_DUP_KEY=ON);
GO
