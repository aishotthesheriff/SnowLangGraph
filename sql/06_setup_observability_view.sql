-- ============================================================================
-- 06_setup_observability_view.sql
-- Real-time Snowflake query telemetry view correlated with LangSmith traces
-- via structured JSON QUERY_TAGs emitted by studio_app.py.
-- ============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE CUSTOMER_INTELLIGENCE_DB;
USE SCHEMA PUBLIC;

CREATE OR REPLACE VIEW CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS AS
SELECT
    QUERY_ID,
    QUERY_TEXT,
    QUERY_TAG,
    TRY_PARSE_JSON(QUERY_TAG):app::STRING AS APP_NAME,
    TRY_PARSE_JSON(QUERY_TAG):trace_id::STRING AS LANGSMITH_TRACE_ID,
    TRY_PARSE_JSON(QUERY_TAG):agent::STRING AS AGENT_NAME,
    TRY_PARSE_JSON(QUERY_TAG):step::NUMBER AS STEP_NUMBER,
    TRY_PARSE_JSON(QUERY_TAG):tool::STRING AS TOOL_NAME,
    EXECUTION_STATUS,
    ERROR_MESSAGE,
    START_TIME,
    END_TIME,
    TOTAL_ELAPSED_TIME AS ELAPSED_MS,
    ROWS_PRODUCED,
    BYTES_SCANNED,
    WAREHOUSE_NAME
FROM TABLE(CUSTOMER_INTELLIGENCE_DB.INFORMATION_SCHEMA.QUERY_HISTORY(
    RESULT_LIMIT => 500
))
WHERE TRY_PARSE_JSON(QUERY_TAG):app::STRING = 'snowlanggraph'
  AND EXECUTION_STATUS != 'RUNNING'
ORDER BY START_TIME DESC;

-- ============================================================================
-- VERIFY
-- ============================================================================
SELECT
    QUERY_ID,
    LANGSMITH_TRACE_ID,
    AGENT_NAME,
    STEP_NUMBER,
    TOOL_NAME,
    EXECUTION_STATUS,
    ELAPSED_MS,
    ROWS_PRODUCED
FROM CUSTOMER_INTELLIGENCE_DB.PUBLIC.AGENT_OBSERVABILITY_LOGS
LIMIT 10;
