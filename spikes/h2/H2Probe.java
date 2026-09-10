import java.sql.*;
public class H2Probe {
  public static void main(String[] a) throws Exception {
    try (Connection c = DriverManager.getConnection("jdbc:h2:mem:p;MODE=Oracle;DATABASE_TO_UPPER=FALSE")) {
      Statement s = c.createStatement();
      s.execute("CREATE TABLE emp (ename VARCHAR, hiredate DATE, sal DOUBLE PRECISION)");
      s.execute("INSERT INTO emp VALUES ('KING', DATE '1981-11-17', 5000)");
      for (String q : a) {
        try (ResultSet rs = s.executeQuery(q)) { rs.next(); StringBuilder sb = new StringBuilder(); for (int i = 1; i <= rs.getMetaData().getColumnCount(); i++) sb.append(rs.getObject(i)).append(" | "); System.out.println("OK   " + q + " -> " + sb); }
        catch (SQLException e) { System.out.println("FAIL " + q + " -> " + e.getMessage().split("\n")[0]); }
      }
    }
  }
}
