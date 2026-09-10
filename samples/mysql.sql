-- MySQL sample
CREATE TABLE `customers` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(100) NOT NULL,
  `tier` ENUM('gold','silver') DEFAULT 'silver',
  `credit` DECIMAL(10,2),
  `is_vip` TINYINT(1) DEFAULT 0,
  `joined` DATETIME,
  `doc` LONGBLOB,
  INDEX idx_name (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE `orders` (
  `customer_id` INT NOT NULL,
  `order_no` BIGINT NOT NULL,
  `status` VARCHAR(20),
  `amount` DOUBLE,
  `ordered_at` TIMESTAMP,
  PRIMARY KEY (`customer_id`, `order_no`),
  FOREIGN KEY (`customer_id`) REFERENCES `customers`(`id`)
);
SELECT `id`, `name` FROM `customers` WHERE `id` = ?;
SELECT id, name FROM customers WHERE name LIKE 'A%' LIMIT 5, 10;
SELECT order_no, amount FROM orders WHERE customer_id = 7 AND status <> 'CANCELLED' ORDER BY order_no DESC LIMIT 20;
SELECT c.name, IFNULL(o.amount, 0) FROM customers c LEFT JOIN orders o ON c.id = o.customer_id;
SELECT status, SUM(amount), AVG(amount) FROM orders GROUP BY status;
SELECT * FROM orders WHERE DATE_FORMAT(ordered_at, '%Y-%m') = '2024-01';
SELECT * FROM orders WHERE customer_id = 7 AND (status = 'NEW' OR status = 'PAID');
SELECT * FROM orders WHERE status IN ('NEW', 'PAID') AND customer_id = 7;
INSERT INTO customers (id, name) VALUES (1, 'Alice'), (2, 'Bob');
INSERT INTO customers (id, name) VALUES (1, 'Alice') ON DUPLICATE KEY UPDATE name = VALUES(name);
REPLACE INTO customers (id, name) VALUES (1, 'Alice');
UPDATE orders SET status = 'PAID' WHERE customer_id = 7 AND order_no = 100;
UPDATE orders o JOIN customers c ON o.customer_id = c.id SET o.status = 'VIP' WHERE c.is_vip = 1;
DELETE FROM orders WHERE customer_id = 7 AND order_no = 100;
START TRANSACTION;
COMMIT;
