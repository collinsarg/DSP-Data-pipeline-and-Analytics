--#2

CREATE SCHEMA [stg];
GO

CREATE TABLE [stg].[TenureReport](
    [row_id]                      int            NULL,
    [dsp]                         nvarchar(16)   NULL,
    [station]                     nvarchar(16)   NULL,
    [year]                        int            NULL,
    [week]                        int            NULL,
    [employee_id]                 int            NULL,
    [transporter_id]              varchar(16)    NULL,
    [name]                        nvarchar(200)  NULL,
    [days_since_last_delivered]   int            NULL,
    [delivery_status]             nvarchar(64)   NULL,
    [driver_status]               nvarchar(64)   NULL,
    [driver_status_reason_code]   nvarchar(128)  NULL,
    [lifetime_routes]             int            NULL,
    [routes_in_week]              int            NULL,
    [tenure_status]               nvarchar(32)   NULL,
    [country]                     nvarchar(16)   NULL
);
