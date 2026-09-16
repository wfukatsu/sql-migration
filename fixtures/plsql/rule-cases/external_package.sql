CREATE OR REPLACE PROCEDURE prc_calls_out(p_url IN VARCHAR2) IS
  v_response VARCHAR2(4000);
BEGIN
  v_response := UTL_HTTP.REQUEST(p_url);
  UPDATE batch_control SET status = 'NOTIFIED' WHERE batch_name = 'SYNC';
END prc_calls_out;
/
