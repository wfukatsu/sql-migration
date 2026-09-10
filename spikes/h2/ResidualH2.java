import java.sql.*;
import java.util.*;

/** Spike: run the ORIGINAL Oracle/PostgreSQL/MySQL SQL on fetched rows using H2 in-memory compatibility modes. */
public class ResidualH2 {
    static String[][] TESTS = {
        {"Oracle", "SELECT ename, NVL(comm, 0) AS comm, sal * 1.1 AS newsal FROM emp WHERE deptno = 30"},
        {"Oracle", "SELECT DISTINCT deptno FROM emp ORDER BY deptno"},
        {"Oracle", "SELECT ename FROM emp WHERE empno IN (SELECT empno FROM bonus)"},
        {"Oracle", "SELECT d.dname, COUNT(*) AS n, SUM(e.sal) AS total FROM emp e JOIN dept d ON e.deptno = d.deptno GROUP BY d.dname HAVING COUNT(*) > 1"},
        {"Oracle", "SELECT UPPER(ename) AS u, CASE WHEN sal > 1500 THEN 'HIGH' ELSE 'LOW' END AS grade FROM emp WHERE SUBSTR(ename, 1, 1) = 'a' OR sal > 2000"},
        {"PostgreSQL", "SELECT ename FROM emp ORDER BY sal DESC LIMIT 2 OFFSET 1"},
        {"PostgreSQL", "SELECT ename FROM emp WHERE deptno = 10 UNION SELECT ename FROM emp WHERE sal > 2000"},
        {"Oracle", "SELECT ename, hiredate FROM emp WHERE hiredate > TO_DATE('2020-06-01','YYYY-MM-DD')"},
        {"Oracle", "SELECT deptno, COUNT(DISTINCT deptno) AS d FROM emp GROUP BY deptno"},
        {"Oracle", "SELECT ename, ROW_NUMBER() OVER (PARTITION BY deptno ORDER BY sal DESC) AS rn FROM emp"},
        {"Oracle", "SELECT ename FROM emp e WHERE EXISTS (SELECT 1 FROM bonus b WHERE b.empno = e.empno)"},
        {"Oracle", "SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno(+) AND ROWNUM <= 3"},
        {"MySQL", "SELECT DATE_FORMAT(hiredate, '%Y-%m') AS ym, IFNULL(comm, 0) AS c FROM emp WHERE deptno = 30"},
        {"Oracle", "SELECT ename, DECODE(deptno, 10, 'A', 30, 'S', '?') AS d, TRUNC(sal / 1000) AS k FROM emp"},
    };

    public static void main(String[] a) throws Exception {
        int ok = 0;
        for (String[] t : TESTS) {
            String url = "jdbc:h2:mem:r;MODE=" + t[0] + ";DATABASE_TO_UPPER=FALSE";
            try (Connection c = DriverManager.getConnection(url)) {
                load(c);
                try (Statement s = c.createStatement(); ResultSet rs = s.executeQuery(t[1])) {
                    List<String> rows = new ArrayList<>();
                    int n = rs.getMetaData().getColumnCount();
                    while (rs.next() && rows.size() < 4) {
                        StringBuilder sb = new StringBuilder("(");
                        for (int i = 1; i <= n; i++) sb.append(i > 1 ? ", " : "").append(rs.getObject(i));
                        rows.add(sb.append(")").toString());
                    }
                    ok++;
                    System.out.println("OK   " + pad(t[1]) + " -> " + rows);
                }
            } catch (SQLException e) {
                System.out.println("FAIL " + pad(t[1]) + " -> " + e.getMessage().split("\n")[0].substring(0, Math.min(80, e.getMessage().split("\n")[0].length())));
            }
        }
        System.out.println("\nH2: " + ok + "/" + TESTS.length);
    }

    static String pad(String s) { return String.format("%-62s", s.substring(0, Math.min(62, s.length()))); }

    static void load(Connection c) throws SQLException {
        try (Statement s = c.createStatement()) {
            // ScalarDB types -> H2 types: INT->INT, TEXT->VARCHAR, DOUBLE->DOUBLE, DATE->DATE
            s.execute("CREATE TABLE emp (empno INT, ename VARCHAR, sal DOUBLE, comm DOUBLE, deptno INT, hiredate DATE)");
            s.execute("INSERT INTO emp VALUES (1,'smith',800,NULL,10,DATE '2021-03-01'),(2,'allen',1600,300,30,DATE '2019-05-02'),(3,'ward',1250,500,30,DATE '2022-01-15'),(4,'jones',2975,NULL,20,DATE '2020-11-11')");
            s.execute("CREATE TABLE dept (deptno INT, dname VARCHAR)");
            s.execute("INSERT INTO dept VALUES (10,'ACCOUNTING'),(20,'RESEARCH'),(30,'SALES')");
            s.execute("CREATE TABLE bonus (empno INT)");
            s.execute("INSERT INTO bonus VALUES (2),(4)");
        }
    }
}
