CREATE OR REPLACE PROCEDURE purge_table (
    p_table_name IN VARCHAR2,
    p_before     IN DATE
) IS
BEGIN
    -- 表の名前を実行時に組み立てる。静的にはどの表を消すのか分からない
    EXECUTE IMMEDIATE 'DELETE FROM ' || p_table_name || ' WHERE logged_at < :d' USING p_before;
END purge_table;
/
